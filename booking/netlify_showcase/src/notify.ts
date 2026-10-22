/* The text of the owner's prototype appointment alerts. Pure functions; nothing here sends anything (this showcase never sends one: the rows are kept in the outbox for the owner's view).
 * Port of aria_booking/appointments/notify.py. One alert per change; each is plainly marked as a prototype alert, names the client and the appointment, and carries nothing else: no phone
 * number, no free-text reason, nothing about any other client. */

import { endUtc, instantOf } from "./appointments.ts";
import type { OutboxMessage, StoredAppointment } from "./appointments.ts";
import type { OutboxBuilder } from "./store.ts";
import { localParts } from "./time.ts";
import type { Instant } from "./time.ts";

export const PREFIX = "[Serenity prototype alert]";
const WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function clock(moment: Instant): string {
  const p = localParts(moment);
  return `${p.hour % 12 || 12}:${String(p.minute).padStart(2, "0")}${p.hour < 12 ? "AM" : "PM"}`;
}
function when(row: StoredAppointment): string {
  const start = instantOf(row.startUtc);
  const p = localParts(start);
  return `${WEEKDAY_SHORT[p.weekday]} ${MONTH_SHORT[p.month - 1]} ${p.day} ${clock(start)}-${clock(instantOf(endUtc(row)))}`;
}
const what = (row: StoredAppointment, technician: string) => `${row.clientName}, ${row.serviceName} with ${technician}, ${when(row)}`;

export const bookedText = (row: StoredAppointment, technician: string) => `${PREFIX} Booked: ${what(row, technician)}. Ref ${row.reference}.`;
export const cancelledText = (row: StoredAppointment, technician: string) => `${PREFIX} Cancelled: ${what(row, technician)}. Ref ${row.reference}.`;
export function rescheduledText(neu: StoredAppointment, old: StoredAppointment, technician: string, oldTechnician: string): string {
  const movedFrom = when(old) + (oldTechnician === technician ? "" : ` with ${oldTechnician}`);
  return `${PREFIX} Rescheduled: ${neu.clientName}, ${neu.serviceName}. Was ${movedFrom}; now ${when(neu)} with ${technician}. Ref ${neu.reference}.`;
}

/** The store's `outbox` callback: (saved, previous) -> the one message for that change. */
export function outboxBuilder(staffName: (staffId: string) => string): OutboxBuilder {
  return (saved, previous): OutboxMessage | null => {
    const technician = staffName(saved.staffId);
    if (saved.status === "cancelled") return { kind: "cancelled", body: cancelledText(saved, technician) };
    if (previous !== null) return { kind: "rescheduled", body: rescheduledText(saved, previous, technician, staffName(previous.staffId)) };
    return { kind: "booked", body: bookedText(saved, technician) };
  };
}
