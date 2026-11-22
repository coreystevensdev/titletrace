"""Recorded vendor responses, so the trace runs with no API keys.

ATTOM's free tier is 200 calls a month and Philadelphia's OPA returns 403 to
most cloud IPs, so a public demo cannot call either and stay up. The recordings
below are injected at the transport layer rather than by stubbing the clients,
which means everything above the socket is the real thing: retry and status
classification in `clients/_base.py`, each client's own parsing, the graph's
five-way fan-out, the fan-in, and the conditional drill-down. A reviewer
reading the trace is reading what that code produced.

What the recordings assert is only what a vendor said. Every parcel field, lien
total, flood zone and routing decision on the demo page is derived from these
payloads by the code that would derive them in production.

The synthesis node is the exception and is not recorded. It calls Claude, and a
response invented here would read as model output while being something a human
typed. The demo shows the real assembled prompt instead, which is `_build_prompt`
running on the real traced state.
"""

from __future__ import annotations


import httpx

# Chosen to exercise different paths rather than to look impressive: one clean
# suburban parcel, one carrying liens so the drill-down branch runs, one in
# Philadelphia so the OPA route is shown instead of ATTOM.
RECORDED_ADDRESSES = [
    "1201 Chestnut St, Philadelphia, PA 19107",
    "45 Lafayette Ave, Haddonfield, NJ 08033",
    "812 Morris Rd, Wayne, PA 19087",
]

_ATTOM = "api.gateway.attomdata.com"
_OPA = "data.phila.gov"
_FEMA = "hazards.fema.gov"


def _attom_property(apn, line1, city, state, postal, owner, lot, year, sub, lat, lon):
    return {
        "property": [{
            "identifier": {"apn": apn},
            "address": {"line1": line1, "city": city, "countrySubd": state, "postal1": postal},
            "owner": {"owner1": {"fullName": owner}},
            "lot": {"lotSize1": lot},
            "summary": {"yearBuilt": year, "propSubType": sub, "propClass": "Residential"},
            "location": {"latitude": str(lat), "longitude": str(lon)},
        }]
    }


_HADDONFIELD_APN = "08-00042-0000-00017"
_WAYNE_APN = "23-05501-0000-00003"

# Keyed by (host, path) with a predicate on params where one endpoint serves
# several parcels. A miss is a 404 on purpose: see test_demo.py.
_RECORDINGS: list[tuple[str, str, object, dict]] = [
    # Philadelphia goes through OPA, not ATTOM.
    # Matched on the queried address, not unconditionally. With predicate=None
    # here, every Philadelphia address returned this parcel: asking for
    # "1600 Market St" answered "1201 CHESTNUT ST" owned by CHESTNUT PARTNERS LP,
    # with the tax and ownership recordings below agreeing, so the whole trace
    # looked like a real answer to a question nobody asked. routes.demo_trace
    # already says "no recorded trace for X, this demo only covers ..." when the
    # parcel comes back None; it never got the chance.
    (_OPA, "/resource/w7rb-qrpr.json",
     lambda p: "chestnut" in (p.get("location") or "").lower(), [{
        "parcel_number": "882026000",
        "location": "1201 CHESTNUT ST",
        "owner_1": "CHESTNUT PARTNERS LP",
        "total_area": "4200",
        "year_built": "1923",
        "category_code_description": "Commercial",
        "zip_code": "19107",
        "lat": "39.950430",
        "lng": "-75.161270",
        "zoning": "CMX-5",
    }]),
    # Keyed on the parcel, like the real endpoint. Only reachable once the lookup
    # above succeeds, but leaving it open made the recording look addressless.
    (_OPA, "/resource/3qem-6v3v.json",
     lambda p: p.get("parcel_number") == "882026000", [{
        "parcel_number": "882026000",
        "total_due": "18422.61",
        "oldest_year_owed": "2021",
    }]),
    (_OPA, "/resource/2upd-bkgb.json",
     lambda p: p.get("opa_account_num") == "882026000", [
        {"opa_account_num": "882026000", "grantees": "CHESTNUT PARTNERS LP",
         "display_date": "2019-06-14T00:00:00.000", "total_consideration": "4150000",
         "document_type": "DEED"},
        {"opa_account_num": "882026000", "grantees": "1201 CHESTNUT ASSOCIATES",
         "display_date": "2006-03-02T00:00:00.000", "total_consideration": "2875000",
         "document_type": "DEED"},
    ]),

    # Zoning goes to ATTOM by street address even for Philadelphia, so the OPA
    # parcel recording above cannot answer it.
    (_ATTOM, "/propertyapi/v1.0.0/property/detail",
     lambda p: "Chestnut" in (p.get("address1") or "") or p.get("apn") == "882026000",
     _attom_property("882026000", "1201 Chestnut St", "Philadelphia", "PA", "19107",
                     "CHESTNUT PARTNERS LP", "4200", "1923", "CMX-5",
                     39.950430, -75.161270)),

    # Liens come from ATTOM even for Philadelphia parcels, because OPA does not
    # serve them. Without this the Philadelphia trace logs two 404s and reports
    # no liens, which reads as a clean title rather than a missing recording.
    (_ATTOM, "/propertyapi/v1.0.0/alllien/detail",
     lambda p: p.get("apn") == "882026000", {"property": [{"liens": [
        {"lienType": "Tax", "lienAmt": "18422.61", "recordingDate": "2022-04-18",
         "lienHolderName": "CITY OF PHILADELPHIA DEPT OF REVENUE", "lienStatus": "open",
         "lienHolderAddr": "1401 John F Kennedy Blvd, Philadelphia, PA 19102"},
        {"lienType": "Mortgage", "lienAmt": "3320000.00", "recordingDate": "2019-06-14",
         "lienHolderName": "FIRST KEYSTONE COMMERCIAL BANK", "lienStatus": "open",
         "lienHolderAddr": "22 W State St, Media, PA 19063",
         "uccFileNum": "2019-06142884"},
     ]}]}),

    # Haddonfield NJ: clean title, no liens, so the drill-down branch is skipped.
    (_ATTOM, "/propertyapi/v1.0.0/property/detail",
     lambda p: "Lafayette" in (p.get("address1") or "") or p.get("apn") == _HADDONFIELD_APN,
     _attom_property(_HADDONFIELD_APN, "45 Lafayette Ave", "Haddonfield", "NJ", "08033",
                     "REYES, MARISOL & REYES, DANIEL", "9148", "1908", "Single Family",
                     39.891400, -75.037600)),
    (_ATTOM, "/propertyapi/v1.0.0/saleshistory/basichistory",
     lambda p: "Lafayette" in (p.get("address1") or ""), {
        "property": [
            {"owner": {"owner1": {"fullName": "REYES, MARISOL & REYES, DANIEL"}},
             "sale": {"saleTransDate": "2017-04-28", "saleAmt": "615000", "transType": "WARRANTY DEED"}},
            {"owner": {"owner1": {"fullName": "OKONKWO, ADAEZE"}},
             "sale": {"saleTransDate": "2009-09-11", "saleAmt": "472500", "transType": "WARRANTY DEED"}},
        ]
     }),
    (_ATTOM, "/propertyapi/v1.0.0/alllien/detail",
     lambda p: p.get("apn") == _HADDONFIELD_APN, {"property": [{"liens": []}]}),

    # Wayne PA: two liens plus an easement, which sends it down the drill-down
    # branch and derives tax delinquency from the lien list rather than OPA.
    (_ATTOM, "/propertyapi/v1.0.0/property/detail",
     lambda p: "Morris" in (p.get("address1") or "") or p.get("apn") == _WAYNE_APN,
     _attom_property(_WAYNE_APN, "812 Morris Rd", "Wayne", "PA", "19087",
                     "HOLLAND, BRIDGET T", "21780", "1964", "Single Family",
                     40.044900, -75.387800)),
    (_ATTOM, "/propertyapi/v1.0.0/saleshistory/basichistory",
     lambda p: "Morris" in (p.get("address1") or ""), {
        "property": [
            {"owner": {"owner1": {"fullName": "HOLLAND, BRIDGET T"}},
             "sale": {"saleTransDate": "2014-11-03", "saleAmt": "538000", "transType": "WARRANTY DEED"}},
            {"owner": {"owner1": {"fullName": "VANCE, GREGORY P & VANCE, ANN"}},
             "sale": {"saleTransDate": "2001-07-19", "saleAmt": "289000", "transType": "WARRANTY DEED"}},
            {"owner": {"owner1": {"fullName": "SULLIVAN, PATRICK J"}},
             "sale": {"saleTransDate": "1987-05-22", "saleAmt": "131500", "transType": "DEED"}},
        ]
     }),
    (_ATTOM, "/propertyapi/v1.0.0/alllien/detail",
     lambda p: p.get("apn") == _WAYNE_APN, {"property": [{"liens": [
        {"lienType": "Tax", "lienAmt": "7318.44", "recordingDate": "2023-02-09",
         "lienHolderName": "DELAWARE COUNTY TAX CLAIM BUREAU", "lienStatus": "open",
         "lienHolderAddr": "201 W Front St, Media, PA 19063"},
        {"lienType": "Mortgage", "lienAmt": "402000.00", "recordingDate": "2014-11-03",
         "lienHolderName": "WELLS FARGO BANK NA", "lienStatus": "open",
         "lienHolderAddr": "101 N Phillips Ave, Sioux Falls, SD 57104",
         "uccFileNum": "2014-11032210"},
        {"lienType": "Encumbrance", "lienComment": "Utility easement along the rear lot line, 15 ft",
         "recordingDate": "1964-08-01", "lienStatus": "recorded"},
     ]}]}),
]

# One FEMA panel per recorded parcel, matched on the coordinates the parcel
# lookup returned. Wiring this on coordinates rather than address is the point:
# it is what proves the FEMA call uses real geometry from the parcel step.
_FEMA_BY_POINT = {
    ("-75.16127", "39.95043"): ("X", "AREA OF MINIMAL FLOOD HAZARD", "42101C0375F", 1549497600000),
    ("-75.0376", "39.8914"): ("AE", "", "34007C0084G", 1551398400000),
    ("-75.3878", "40.0449"): ("X", "AREA OF MINIMAL FLOOD HAZARD", "42045C0168G", 1518220800000),
}


def _fema_response(params: dict) -> dict | None:
    geometry = params.get("geometry", "")
    lon, _, lat = geometry.partition(",")
    key = (str(float(lon)) if lon else "", str(float(lat)) if lat else "")
    for (klon, klat), (zone, subty, panel, eff) in _FEMA_BY_POINT.items():
        if (str(float(klon)), str(float(klat))) == key:
            return {"features": [{"attributes": {
                "FLD_ZONE": zone, "ZONE_SUBTY": subty,
                "FIRM_PANEL": panel, "EFF_DATE": eff,
            }}]}
    return {"features": []}


def _handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    params = dict(request.url.params)

    if host == _FEMA:
        body = _fema_response(params)
        if body is None:
            return httpx.Response(404, json={"error": "no recording"})
        return httpx.Response(200, json=body)

    for rec_host, rec_path, predicate, body in _RECORDINGS:
        if host != rec_host or path != rec_path:
            continue
        if predicate is not None and not predicate(params):
            continue
        return httpx.Response(200, json=body)

    # Loud on purpose. A silently empty body renders as "no liens found",
    # which is a real-looking answer to a question that was never asked.
    return httpx.Response(404, json={
        "error": "no recording", "host": host, "path": path, "params": params,
    })


def recorded_transport() -> httpx.MockTransport:
    return httpx.MockTransport(_handler)


def demo_client() -> httpx.AsyncClient:
    """An httpx client that answers from the recordings. Real client object,
    real retry and parsing paths, scripted socket."""
    return httpx.AsyncClient(transport=recorded_transport())
