import {t as ui} from "./i18n";
import { useEffect, useRef, useState } from "react";
import { api, type Project } from "./api";
type Props = {
    project: Project;
    onClose: () => void;
    onDeleted: () => Promise<void>;
};
export function ProjectTrashDialog({ project, onClose, onDeleted }: Props) {
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const panel = useRef<HTMLElement>(null);
    const cancel = useRef<HTMLButtonElement>(null);
    const busyRef = useRef(busy);
    const closeRef = useRef(onClose);
    busyRef.current = busy;
    closeRef.current = onClose;
    useEffect(() => {
        const previousFocus = document.activeElement;
        cancel.current?.focus();
        const listener = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                event.preventDefault();
                if (!busyRef.current)
                    closeRef.current();
            }
            if (event.key !== "Tab")
                return;
            const buttons = panel.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
            if (!buttons?.length) {
                event.preventDefault();
                return;
            }
            const first = buttons[0], last = buttons[buttons.length - 1];
            if (!panel.current?.contains(document.activeElement)) {
                event.preventDefault();
                (event.shiftKey ? last : first).focus();
            }
            else if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            }
            else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        };
        window.addEventListener("keydown", listener);
        return () => {
            window.removeEventListener("keydown", listener);
            if (previousFocus instanceof HTMLElement && previousFocus.isConnected)
                previousFocus.focus();
            else
                document.querySelector<HTMLInputElement>(ui("[aria-label=\"搜尋側欄作品\"]"))?.focus();
        };
    }, []);
    async function remove() {
        if (busyRef.current)
            return;
        busyRef.current = true;
        setBusy(true);
        setError("");
        try {
            await api(`/projects/${project.id}`, "DELETE");
            await onDeleted();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            busyRef.current = false;
            setBusy(false);
        }
    }
    return (<div className="modal-backdrop">
      <section ref={panel} className="modal" role="dialog" aria-modal="true" aria-labelledby="trash-title" aria-describedby="trash-description" aria-busy={busy}>
        <h2 id="trash-title">{ui("確定刪除「")}{project.title}」？</h2>
        <p id="trash-description">{ui("作品會移至垃圾桶。原始音檔、轉錄、譯文、人工校對與匯出檔案都會保留，可在「作品管理 → 垃圾桶」還原。")}</p>
        {error && <div className="error" role="alert">{error}</div>}
        <div className="manager-actions">
          <button ref={cancel} disabled={busy} onClick={onClose}>{ui("取消")}</button>
          <button className="danger" disabled={busy} onClick={() => void remove()}>{busy ? ui("刪除中…") : ui("確認刪除")}</button>
        </div>
      </section>
    </div>);
}
