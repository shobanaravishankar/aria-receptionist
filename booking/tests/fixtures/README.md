# Fixtures: real captured pages (redacted structure)

These are slimmed copies of reports produced by `python -m aria_booking discover` / `discover-steps` against the
dedicated Booksy test account on 8 October 2026.

- Text was blanked character by character at capture time (times, small numbers and known UI words are kept; names,
  phone numbers and emails are masked); attribute **values** were never recorded; the business id is masked in URLs.
- Decoration nodes (svg, path, img, hr, iframe) were dropped to keep them small.
- Before writing, each file was scanned for the business id, a person's name, email shapes and long digit runs.

| file | what it is |
|---|---|
| `busy_day_thu_8_oct.json` | a day with exactly one appointment (3:15-5:45 PM, the fictional test booking) |
| `empty_day_mon_12_oct.json`, `empty_day_sun_11_oct.json` | days with no appointments |
| `staff_page_one_member.json` | the Staff page of an account with exactly one staff member (the masked email node was dropped and internal ids blanked) |
| `form_open_mon_12_oct.json` | a page with the New Appointment drawer open and a draft card; the reader must refuse it |

Do not add captures that were not produced by the redacting tool.
