/* Plain data types for the availability rules. No I/O. Port of aria_booking/models.py: an Interval is half-open [start, end) so touching intervals do not overlap; unknown (null) is never free. */

import { hhmm, MINUTE, addMinutes } from "./time.ts";
import type { DateParts, Instant } from "./time.ts";

export class Interval {
  readonly start: Instant;
  readonly end: Instant;
  constructor(start: Instant, end: Instant) {
    if (end <= start) throw new Error("Interval end must be after start");
    this.start = start;
    this.end = end;
  }
  overlaps(other: Interval): boolean {
    return this.start < other.end && other.start < this.end;
  }
  contains(other: Interval): boolean {
    return this.start <= other.start && other.end <= this.end;
  }
  get minutes(): number {
    return Math.floor((this.end - this.start) / MINUTE);
  }
  label(): string {
    return `${hhmm(this.start)}-${hhmm(this.end)}`;
  }
}

export interface ServiceSpec {
  name: string;
  durationMinutes: number;
  bufferBeforeMinutes: number;
  bufferAfterMinutes: number;
}

export interface Appointment {
  staff: string;
  interval: Interval;
  service: string;
  note: string;
  blocksTime: boolean;
}

/** `null` means UNKNOWN (could not be read); an empty array means KNOWN to be empty. */
export interface StaffDay {
  staff: string;
  working: Interval[] | null;
  timeOff: Interval[] | null;
  appointments: Appointment[] | null;
  staffId: string;
}

export interface DaySnapshot {
  day: DateParts;
  staffDays: StaffDay[];
  capturedAt: Instant;
}

export function forStaff(snapshot: DaySnapshot, staff: string): StaffDay | null {
  const wanted = staff.trim().toLowerCase();
  for (const sd of snapshot.staffDays) if (sd.staff.trim().toLowerCase() === wanted) return sd;
  return null;
}

export interface Slot {
  staff: string;
  service: Interval; // what the client occupies
  blocked: Interval; // service plus buffers
}

export { addMinutes };
