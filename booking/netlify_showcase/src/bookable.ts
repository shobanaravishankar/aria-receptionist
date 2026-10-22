/* The allowlist of services Aria may BOOK. Port of aria_booking/catalog/bookable.py (the parts the hosted showcase uses).
 *
 * A service is bookable only if it is in this registry with a duration, optional buffers and a staff-eligibility set. Duration, price and the booked service name come ONLY from here:
 * never from the caller and never from the voice model. A menu item with no entry here can be discussed and never booked. */

import { Catalog, SHOWCASE_CATALOG } from "./catalog.ts";

/** A comparable key for a staff display name. */
export function staffKey(name: string): string {
  return (name || "").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
}

export interface BookableService {
  serviceId: string;
  booksyName: string; // the name the service is booked and shown under
  durationMinutes: number;
  bufferBeforeMinutes: number;
  bufferAfterMinutes: number;
  /** Staff keys allowed to perform it; null means any staff member the calendar lists; empty means nobody. */
  eligibleStaff: ReadonlySet<string> | null;
  /** STABLE staff ids allowed to perform it. When set it takes precedence over names: a rename cannot change who qualifies. */
  eligibleStaffIds: ReadonlySet<string> | null;
  verified: boolean;
  catalogItem: string | null;
  testOnly: boolean;
  priceUsd: number | null;
}

export function bookable(fields: Pick<BookableService, "serviceId" | "booksyName" | "durationMinutes"> & Partial<BookableService>): BookableService {
  return {
    bufferBeforeMinutes: 0, bufferAfterMinutes: 0, eligibleStaff: null, eligibleStaffIds: null, verified: false, catalogItem: null, testOnly: false, priceUsd: null, ...fields,
  };
}

export function isEligible(service: BookableService, staffName: string, staffId = ""): boolean {
  if (service.eligibleStaffIds !== null) return Boolean(staffId) && service.eligibleStaffIds.has(staffId); // a person whose id is unknown is never eligible
  return service.eligibleStaff === null || service.eligibleStaff.has(staffKey(staffName));
}

export class BookableRegistry {
  private readonly services = new Map<string, BookableService>();
  readonly defaultId: string | null;

  constructor(services: Iterable<BookableService>, opts: { catalog?: Catalog; defaultId?: string | null } = {}) {
    const catalog = opts.catalog ?? SHOWCASE_CATALOG;
    for (const service of services) {
      if (this.services.has(service.serviceId)) throw new Error(`duplicate bookable service id '${service.serviceId}'`);
      if (service.durationMinutes <= 0) throw new Error(`${service.serviceId}: duration must be positive`);
      if (service.catalogItem !== null) {
        const item = catalog.get(service.catalogItem);
        if (item === null) throw new Error(`${service.serviceId}: unknown catalogue item '${service.catalogItem}'`);
        if (item.durationMinutes !== null && item.durationMinutes !== service.durationMinutes) {
          throw new Error(`${service.serviceId}: the booked duration (${service.durationMinutes}) differs from the listed duration (${item.durationMinutes}) for ${item.itemId}; reconcile before enabling`);
        }
      }
      this.services.set(service.serviceId, service);
    }
    const defaultId = opts.defaultId ?? null;
    if (defaultId !== null && !this.services.has(defaultId)) throw new Error(`default service '${defaultId}' is not in the registry`);
    this.defaultId = defaultId;
  }

  get(serviceId: unknown): BookableService | null {
    return typeof serviceId === "string" ? (this.services.get(serviceId) ?? null) : null;
  }
  isBookable(serviceId: unknown): boolean {
    return this.get(serviceId) !== null;
  }
  ids(): string[] {
    return [...this.services.keys()];
  }
  /** The bookable service mapped to a menu item, if any (a menu item with none can only be discussed). */
  forCatalogItem(itemId: string): BookableService | null {
    for (const service of this.services.values()) if (service.catalogItem === itemId) return service;
    return null;
  }
}
