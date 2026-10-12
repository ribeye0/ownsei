export type TermEntry = {ja: string; zh: string; enabled: boolean};
export function readEntries(text: string, strict = true): TermEntry[] {
    const input: unknown = JSON.parse(text);
    const value = Array.isArray(input) ? input : input && typeof input === "object"
        ? Object.entries(input).map(([ja, zh]) => ({ja, zh, enabled: true})) : null;
    if (!value) throw new Error("術語表須為 JSON 陣列或日中對照物件");
    const seen = new Set<string>();
    return value.map(row => {
        if (!row || typeof row !== "object" || Object.keys(row).some(key => !["ja", "zh", "enabled"].includes(key)) ||
            typeof row.ja !== "string" || typeof row.zh !== "string" || (row.enabled !== undefined && typeof row.enabled !== "boolean"))
            throw new Error("每條術語須包含日文 ja、中文 zh，以及布林值 enabled");
        const ja = strict ? row.ja.trim() : row.ja, zh = strict ? row.zh.trim() : row.zh;
        if (strict && (!ja || !zh)) throw new Error("術語的日文與中文不可空白");
        if (strict && seen.has(ja)) throw new Error("術語日文重複：" + ja);
        seen.add(ja);
        return {ja, zh, enabled: row.enabled ?? true};
    });
}
