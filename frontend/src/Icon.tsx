type Name =
  | "audio"
  | "folder"
  | "plus"
  | "settings"
  | "models"
  | "book"
  | "download"
  | "export"
  | "layers"
  | "play"
  | "list"
  | "check"
  | "chevron"
  | "trash"
  | "wave"
  | "edit"
  | "flag";
const paths: Record<Name, string> = {
  edit: "m15 4 5 5-11 11-6 1 1-6L15 4ZM12 7l5 5",
  flag: "M5 21V3m0 1c5-4 9 4 15 0v10c-6 4-10-4-15 0",
  audio:
    "M9 18V5l12-2v13M9 8l12-2M9 18a3 3 0 1 1-3-3c1.7 0 3 1 3 3Zm12-2a3 3 0 1 1-3-3c1.7 0 3 1 3 3Z",
  folder: "M3 7V5h6l2 2h10v13H3V7Z",
  plus: "M12 5v14M5 12h14",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2",
  models: "M5 3h14v18H5V3ZM9 7h6M9 12h6M9 17h6",
  book: "M12 5C8 2 3 3 3 3v16s5-1 9 2c4-3 9-2 9-2V3s-5-1-9 2v16",
  export: "M12 15V3m-5 5 5-5 5 5M4 16v5h16v-5",
  download: "M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5",
  layers: "m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5",
  play: "m9 5 11 7-11 7V5Z",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1",
  check: "m5 12 4 4L19 6",
  chevron: "m9 5 7 7-7 7",
  trash: "M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7",
  wave: "M3 10v4M7 6v12M12 3v18M17 6v12M21 10v4",
};
export function Icon({ name }: { name: Name }) {
  return (
    <svg
      className="ui-icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}
