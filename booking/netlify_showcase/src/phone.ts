/* Phone number normalisation that never invents a digit. Port of aria_booking/history/phone.py.
 *
 * A complete phone number as "+<digits>", or null when it is not complete or not understood. Formatting (spaces, dashes, dots, brackets) is ignored. Letters make it not understood.
 * With a leading "+" the caller gave the country code: 8 to 15 digits. Otherwise the salon's country (US, +1) is assumed ONLY for a full ten-digit number, or eleven digits starting with 1;
 * an area code never starts with 0 or 1. Known difference: digits are ASCII only here (Python's \d would also accept other scripts' digits, which are simply not understood). */

export function normalizePhone(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!text || text.length > 40 || /[^0-9\s().+\-]/.test(text)) return null; // letters (an extension, "five five"), punctuation and symbols: not understood
  const digits = text.replace(/\D/g, "");
  if (text.startsWith("+")) return digits.length >= 8 && digits.length <= 15 && digits[0] !== "0" ? "+" + digits : null;
  if (text.includes("+")) return null;
  if (digits.length === 10 && !"01".includes(digits[0]) && !"01".includes(digits[3])) return "+1" + digits;
  if (digits.length === 11 && digits[0] === "1" && !"01".includes(digits[1]) && !"01".includes(digits[4])) return "+" + digits;
  return null;
}
