/* The demo's seeded fictional clients and their appointments, so a test has something to find, change and cancel. Port of aria_booking/appointments/seed.py.
 * Everything here is invented: the names are fictional and the phone numbers are in the 555-01xx range North America reserves for fiction. Nothing here sends anything.
 * Identity is stable: every seeded row has a seed key and its reference is derived from it. Past visits are seeded once; upcoming ones are topped up when the world opens. */

import { CANCELLED, COMPLETED, epochOf, NO_SHOW, ORIGIN_SEED } from "./appointments.ts";
import type { NewBooking } from "./appointments.ts";
import { isEligible } from "./bookable.ts";
import type { PlacementJudge } from "./judge.ts";
import type { Store } from "./store.ts";
import { addDays, dateOf, fromLocal, isoDate, weekdayOf } from "./time.ts";
import type { Instant } from "./time.ts";

type Client = readonly [name: string, phone: string];
export const WILLA: Client = ["Willa Hartwell", "+12125550142"];
export const ANSEL: Client = ["Ansel Hartwell", "+12125550142"]; // the same household number: a name is needed to tell them apart
export const CEDRIC: Client = ["Cedric Lowe", "+12125550177"]; // history only: no upcoming appointment
export const SEEDED_CLIENTS = [WILLA, ANSEL, CEDRIC];

// (template, client, weekday with Monday = 0, service id, candidate start times in minutes since midnight, tried in this order)
const UPCOMING: Array<[string, Client, number, string, number[]]> = [
  ["tue-waxing", WILLA, 1, "waxing-full-leg-40", [900, 840, 960, 780, 1020]],
  ["fri-head-spa", WILLA, 4, "head-spa-head-scalp-massage-30", [660, 720, 780, 840, 900]],
  ["sun-massage", WILLA, 6, "massage-swedish-massage-60", [840, 780, 900, 720, 960]],
  ["thu-express-facial", ANSEL, 3, "facial-express-facial-30", [1020, 960, 900, 840, 780]],
];
// (template, client, days before the demo starts, service id, final status): past visits, made once
const HISTORY: Array<[string, Client, number, string, string]> = [
  ["lash-lifting", WILLA, 21, "lash-eyelash-lifting-45", COMPLETED],
  ["swedish-massage", WILLA, 14, "massage-swedish-massage-60", COMPLETED],
  ["signature-facial", WILLA, 42, "facial-signature-facial-60", COMPLETED],
  ["cancelled-lash", WILLA, 7, "lash-eyelash-lifting-45", CANCELLED],
  ["no-show-facial", WILLA, 28, "facial-signature-facial-60", NO_SHOW],
  ["deep-tissue", ANSEL, 10, "massage-deep-tissue-massage-60", COMPLETED],
  ["signature-facial", CEDRIC, 30, "facial-signature-facial-60", COMPLETED],
];
const WEEKS_TRIED = 4;
const FALLBACK_TIMES = Array.from({ length: (19 * 60 - 10 * 60) / 15 + 1 }, (_, i) => 10 * 60 + i * 15); // after the preferred times, any quarter hour of the working day

export interface SeedReport {
  upcomingAdded: Array<[string, string]>; // (template, reference)
  upcomingSkipped: string[]; // templates that could not be placed
  historyAdded: number;
}

const key = (prefix: string, template: string, client: Client, suffix = "") => `${prefix}:${client[0].split(/\s+/)[0].toLowerCase()}:${template}` + (suffix ? `:${suffix}` : "");

/** Make whatever seeded rows are missing. Safe to run on every start: a row that exists is never changed, duplicated or revived. */
export async function seedDemo(store: Store, judge: PlacementJudge, clock: () => Instant): Promise<SeedReport> {
  const report: SeedReport = { upcomingAdded: [], upcomingSkipped: [], historyAdded: 0 };
  const now = clock();
  const registry = judge.dataset.registry;
  const roster = judge.dataset.roster;
  const eligibleStaff = (service: NonNullable<ReturnType<typeof registry.get>>) => roster.filter((s) => s.hoursKnown && isEligible(service, s.name, s.staffId));

  for (const [template, client, daysAgo, serviceId, status] of HISTORY) {
    const service = registry.get(serviceId)!;
    const staff = eligibleStaff(service)[0];
    const day = addDays(dateOf(now), -daysAgo);
    const when = fromLocal(day.year, day.month, day.day, 11, 0);
    const booking: NewBooking = {
      clientName: client[0], phone: client[1], serviceId: service.serviceId, serviceName: service.booksyName, category: judge.dataset.categoryOf(service.serviceId), staffId: staff.staffId,
      startUtc: epochOf(when), durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes,
      origin: ORIGIN_SEED, seedKey: key("history", template, client),
    };
    const outcome = await store.addHistory(booking, status);
    if (outcome.status === "booked") report.historyAdded += 1;
  }

  for (const [template, client, weekday, serviceId, times] of UPCOMING) {
    if (await store.seededAfter(key("upcoming", template, client) + ":", epochOf(now))) continue;
    const service = registry.get(serviceId)!;
    let first = addDays(dateOf(now), 2); // at least two days out, so the minimum-notice rule is never what stops a seed
    while (weekdayOf(first) !== weekday) first = addDays(first, 1);
    let placed = false;
    for (let week = 0; week < WEEKS_TRIED && !placed; week++) { // a crowded generated day moves the seed to the same weekday a week later, never to another weekday
      const day = addDays(first, 7 * week);
      for (const minutes of [...new Set([...times, ...FALLBACK_TIMES])]) {
        for (const staff of eligibleStaff(service)) {
          const start = fromLocal(day.year, day.month, day.day, Math.floor(minutes / 60), minutes % 60);
          const outcome = await store.book(
            {
              clientName: client[0], phone: client[1], serviceId: service.serviceId, serviceName: service.booksyName, category: judge.dataset.categoryOf(service.serviceId), staffId: staff.staffId,
              startUtc: epochOf(start), durationMinutes: service.durationMinutes, bufferBeforeMinutes: service.bufferBeforeMinutes, bufferAfterMinutes: service.bufferAfterMinutes,
              origin: ORIGIN_SEED, seedKey: key("upcoming", template, client, isoDate(day)),
            },
            { validate: async (rowsBetween, placement) => void (await judge.require(placement, rowsBetween)) },
          );
          if (outcome.status === "booked" || outcome.status === "replayed") {
            report.upcomingAdded.push([template, outcome.appointment!.reference]);
            placed = true;
            break;
          }
        }
        if (placed) break;
      }
    }
    if (!placed) report.upcomingSkipped.push(template);
  }
  return report;
}

