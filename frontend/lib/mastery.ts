/** Red (0) through amber to green (1), as a pale HSL fill. */
export function masteryColor(p: number): string {
  const clamped = Math.min(1, Math.max(0, p));
  return `hsl(${Math.round(clamped * 120)} 70% 85%)`;
}
