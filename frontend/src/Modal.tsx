import {t as ui} from "./i18n";
import { useEffect, useRef, type ReactNode } from "react";
export function Modal({ title, children, onClose, busy = false, className = "" }: {
    title: string;
    children: ReactNode;
    onClose: () => void;
    busy?: boolean;
    className?: string;
}) {
    const panel = useRef<HTMLElement>(null), close = useRef(onClose), blocked = useRef(busy);
    close.current = onClose;
    blocked.current = busy;
    useEffect(() => {
        const previous = document.activeElement;
        const items = () => Array.from(panel.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], summary') || []).filter(e => e.getClientRects().length > 0);
        items()[0]?.focus();
        const key = (e: KeyboardEvent) => {
            if(Array.from(document.querySelectorAll('[aria-modal="true"]')).at(-1)!==panel.current) return;
            if (e.key === "Escape") {
                e.preventDefault();
                e.stopImmediatePropagation();
                if (!blocked.current)
                    close.current();
            }
            if (e.key !== "Tab")
                return;
            const list = items(), first = list[0], last = list.at(-1);
            if (!first) {
                e.preventDefault();
                return;
            }
            if (!panel.current?.contains(document.activeElement)) {
                e.preventDefault();
                first.focus();
            }
            else if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last?.focus();
            }
            else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        };
        window.addEventListener("keydown", key, true);
        return () => { window.removeEventListener("keydown", key, true); if (previous instanceof HTMLElement && previous.isConnected)
            previous.focus(); };
    }, []);
    return <div className="modal-backdrop"><section ref={panel} className={`modal ${className}`} role="dialog" aria-modal="true" aria-label={title} aria-busy={busy}>
    <button className="modal-close icon" disabled={busy} aria-label={ui("關閉視窗")} onClick={onClose}>×</button>
    <h2>{title}</h2>{children}
  </section></div>;
}
