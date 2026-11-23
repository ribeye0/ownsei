export const PROJECT_NUMBER_KEY = "ownsei:project-number";

export function projectNumber(title: string): number {
  const match = /^(?:專案|项目|項目)\s*0*(\d+)$/.exec(title.trim());
  const value = match ? Number(match[1]) : 0;
  return Number.isSafeInteger(value) ? value : 0;
}

export function nextProjectTitle(
  titles: string[],
  prefix: string,
  lastUsed = 0,
): string {
  const highest = Math.max(
    Number.isSafeInteger(lastUsed) && lastUsed >= 0 ? lastUsed : 0,
    0,
    ...titles.map(projectNumber),
  );
  return `${prefix} ${highest + 1}`;
}

export function nextProjectSelection(
  previous: string[],
  available: string[],
  active: string,
): string {
  if (!active || available.includes(active)) return active;
  const index = Math.max(0, previous.indexOf(active));
  return available[Math.min(index, available.length - 1)] || "";
}
