/* Plain-English phrases for the voice agent. Pure functions; no personal data. Port of aria_booking/voice/speech.py. */

import { localParts } from "./time.ts";
import type { Instant } from "./time.ts";

const WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function spokenTime(moment: Instant): string {
  const p = localParts(moment);
  const hour = p.hour % 12 || 12;
  const suffix = p.hour < 12 ? "AM" : "PM";
  return p.minute === 0 ? `${hour} ${suffix}` : `${hour}:${String(p.minute).padStart(2, "0")} ${suffix}`;
}

export function spokenDay(moment: Instant): string {
  const p = localParts(moment);
  return `${WEEKDAY_NAMES[p.weekday]}, ${MONTH_NAMES[p.month - 1]} ${p.day}`;
}

export function spokenSlot(moment: Instant): string {
  return `${spokenDay(moment)} at ${spokenTime(moment)}`;
}

export function spokenDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  const parts: string[] = [];
  if (hours) parts.push(`${hours} hour` + (hours !== 1 ? "s" : ""));
  if (rest) parts.push(`${rest} minutes`);
  return parts.join(" ") || "0 minutes";
}

/** 'A', 'A or B', 'A, B, or C': the caller picks one. */
export function joinChoices(labels: readonly string[]): string {
  if (labels.length === 1) return labels[0];
  return labels.slice(0, -1).join(", ") + ", or " + labels[labels.length - 1];
}

/** 'A', 'A and B', 'A, B and C': a list of people who ALL do something. */
export function joinNames(names: readonly string[]): string {
  if (names.length === 1) return names[0];
  return names.slice(0, -1).join(", ") + " and " + names[names.length - 1];
}

export function spokenPrice(priceUsd: number | null | undefined): string {
  return priceUsd === null || priceUsd === undefined ? "the price isn't listed" : `$${priceUsd}`;
}
