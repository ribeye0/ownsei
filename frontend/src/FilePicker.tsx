import {t as ui} from "./i18n";
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Modal } from "./Modal";
import { RequestLane } from "./requestLane";
type Listing = {
    path: string;
    parent: string;
    entries: {
        name: string;
        path: string;
        directory: boolean;
    }[];
};
export function FilePicker({ initial, onSelect, onClose }: {
    initial: string;
    onSelect: (path: string) => void;
    onClose: () => void;
}) {
    const [listing, setListing] = useState<Listing | null>(null), [path, setPath] = useState(initial), [selected, setSelected] = useState("");
    const [loading, setLoading] = useState(false), [error, setError] = useState("");
    const reads = useRef(new RequestLane(setLoading));
    async function browse(next: string) {
        setError("");
        setSelected("");
        try {
            await reads.current.run(signal => api<Listing>("/local-files" + (next ? "?path=" + encodeURIComponent(next) : ""), "GET", undefined, { signal }), value => { setListing(value); setPath(value.path); });
        }
        catch (e) {
            setError(String(e));
        }
    }
    useEffect(() => { void browse(initial); return () => reads.current.cancel(); }, []);
    return <Modal title={ui("選擇本機音檔或資料夾")} onClose={onClose} className="file-picker">
    <form className="picker-path" onSubmit={e => { e.preventDefault(); void browse(path); }}><input aria-label={ui("瀏覽目錄路徑")} value={path} onChange={e => setPath(e.target.value)}/><button disabled={loading}>{ui("前往")}</button></form>
    <button disabled={loading || !listing || listing.parent === listing.path} onClick={() => listing && void browse(listing.parent)}>{ui("上一層目錄")}</button>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="picker-list" aria-busy={loading}>{loading ? <p role="status">{ui("讀取目錄…")}</p> : listing?.entries.map(entry => <button key={entry.path} className={selected === entry.path ? "selected" : ""} onClick={() => entry.directory ? void browse(entry.path) : setSelected(entry.path)} title={entry.path}>
      <span aria-hidden="true">{entry.directory ? "[DIR]" : "[AUDIO]"}</span> {entry.name}
    </button>)}</div>
    {!loading && !listing?.entries.length && <p>{ui("此目錄沒有可匯入的音檔或資料夾")}</p>}
    <small>{ui("音檔保持在原位置；選資料夾會掃描其子目錄")}</small>
    <div className="manager-actions"><button disabled={loading || !listing} onClick={() => listing && onSelect(listing.path)}>{ui("選擇此資料夾")}</button><button className="primary" disabled={loading || !selected} onClick={() => onSelect(selected)}>{ui("選擇音檔")}</button></div>
  </Modal>;
}
