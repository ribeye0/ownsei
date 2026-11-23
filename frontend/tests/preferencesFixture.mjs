import { after } from "node:test";
import { createServer } from "vite";

// Load the actual TypeScript modules together so the preferences store is shared.
export async function preferencesFixture(module) {
  const vite = await createServer({configFile: false, server: {middlewareMode: true, hmr: false, watch: null}});
  after(() => vite.close());
  const settings = await vite.ssrLoadModule("/src/interfacePreferences.ts");
  const client = await vite.ssrLoadModule("/src/" + module + ".ts");
  const legacy = new Map();
  let disk = {};
  let failWrites = false;
  globalThis.window = {dispatchEvent() {}};
  globalThis.localStorage = {getItem: key => legacy.get(key) ?? null};
  globalThis.fetch = async (_url, options) => {
    const {values, import_missing} = JSON.parse(options.body);
    if (!import_missing && failWrites) throw Error("disk write failed");
    disk = import_missing ? {...values, ...disk} : {...disk, ...values};
    return Response.json(disk);
  };
  return {client, legacy, settings, disk: () => disk, failWrites: value => {failWrites = value;},
    reset: async (initial = {}) => {await settings.loadInterfacePreferences(); disk = initial; legacy.clear(); await settings.loadInterfacePreferences();}};
}
