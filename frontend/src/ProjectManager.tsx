import {t as ui} from "./i18n";
import { useEffect, useState } from "react";
import {Modal} from "./Modal";
import { api, type Project } from "./api";
type Props = {
    onClose: () => void;
    onChanged: () => Promise<void>;
    onOpen: (id: string) => void;
};
type EmptyTrashResult = {
    deleted: {id: string; scope: string}[];
    failed: {id: string; error: string}[];
};
export function ProjectManager({ onClose, onChanged, onOpen }: Props) {
    const [active, setActive] = useState<Project[]>([]);
    const [trash, setTrash] = useState<Project[]>([]);
    const [tab, setTab] = useState("active");
    const [search, setSearch] = useState("");
    const [sort, setSort] = useState("newest");
    const [editing, setEditing] = useState("");
    const [title, setTitle] = useState("");
    const [deleting, setDeleting] = useState("");
    const [purge, setPurge] = useState<{
        project: Project;
        scope: string;
        path: string;
    } | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [emptyTarget, setEmptyTarget] = useState<Project[] | null>(null);
    const [emptyResult, setEmptyResult] = useState<{deleted: number; failed: {title: string; error: string}[]} | null>(null);
    async function load() {
        const [projects, removed] = await Promise.all([
            api<Project[]>("/projects"),
            api<Project[]>("/projects?trashed=true"),
        ]);
        setActive(projects);
        setTrash(removed);
    }
    useEffect(() => {
        load().catch((e) => setError(String(e)));
    }, []);
    async function change(action: () => Promise<unknown>) {
        setBusy(true);
        setError("");
        setEmptyResult(null);
        try {
            await action();
            setEditing("");
            setDeleting("");
            setPurge(null);
            setEmptyTarget(null);
            await load();
            await onChanged();
        }
        catch (e) {
            setError(String(e));
        }
        finally {
            setBusy(false);
        }
    }
    const query = search.trim().toLocaleLowerCase();
    const projects = (tab === "active" ? active : trash)
        .filter((p) => `${p.title} ${p.path}`.toLocaleLowerCase().includes(query))
        .sort((a, b) => sort === "title"
        ? a.title.localeCompare(b.title, "zh-Hant", { numeric: true })
        : (b.trashed_at || b.created_at || 0) -
            (a.trashed_at || a.created_at || 0));
    function switchTab(next: string) {
        setTab(next);
        setEditing("");
        setDeleting("");
    }
    async function clearTrash() {
        if (!emptyTarget) return;
        const targets = emptyTarget;
        await change(async () => {
            const result = await api<EmptyTrashResult>("/trash/purge", "DELETE", {project_ids: targets.map(p => p.id)}, {timeoutMs: 180000});
            setEmptyResult({deleted: result.deleted.length, failed: result.failed.map(f => ({title: targets.find(p => p.id === f.id)?.title || f.id, error: f.error}))});
        });
    }
    return (<Modal title={ui("作品管理")} onClose={onClose} busy={busy} className="project-manager">
        <div className="manager-tabs">
          <button aria-pressed={tab === "active"} onClick={() => switchTab("active")}>{ui("我的作品 (")}{active.length})
          </button>
          <button aria-pressed={tab === "trash"} onClick={() => switchTab("trash")}>{ui("垃圾桶 (")}{trash.length})
          </button>
        </div>
        <p>{ui("刪除會移至垃圾桶，可隨時還原。原音、轉錄、校對和匯出檔案都會保留。")}</p>
        <div className="manager-tools">
          <input autoFocus aria-label={ui("搜尋作品名稱或路徑")} placeholder={ui("搜尋作品名稱或路徑")} value={search} onChange={(e) => setSearch(e.target.value)}/>
          <label>{ui("排序")}<select value={sort} onChange={(e) => setSort(e.target.value)}>
              <option value="newest">{ui("最新在前")}</option>
              <option value="title">{ui("名稱順序")}</option>
            </select>
          </label>
          {tab === "trash" && <button className="danger" disabled={busy || !trash.length} onClick={() => {setError(""); setEmptyResult(null); setEmptyTarget([...trash]);}}>{ui("清空垃圾桶")}</button>}
        </div>
        {error && <div className="error" role="alert">{error}</div>}
        {emptyResult && <div className="trash-result" role="status">
          <p>{ui("已永久移除 ")}{emptyResult.deleted}{ui(" 部作品；保留 ")}{emptyResult.failed.length}{ui(" 部未能移除的作品")}</p>
          {emptyResult.failed.length > 0 && <ul>{emptyResult.failed.map((f, i) => <li key={i}><strong>{f.title}</strong>：{f.error}</li>)}</ul>}
        </div>}
        {emptyTarget && <Modal title={ui("清空垃圾桶確認")} onClose={() => setEmptyTarget(null)} busy={busy} className="purge-confirm">
          {error && <p className="error" role="alert">{error}</p>}
          <p>{ui("永久移除垃圾桶內全部 ")}{emptyTarget.length}{ui(" 部作品？包含未顯示在搜尋結果中的作品，此操作無法還原。")}</p>
          <p>{ui("Ownsei 工作資料（轉錄、譯文、人工校對、快取、日誌與匯出檔）將永久刪除，原始音檔保留。外部專案只移除登錄；受保護或刪除失敗的作品會留在垃圾桶，並列出原因。")}</p>
          <ul className="trash-targets">{emptyTarget.map(p => <li key={p.id}>{p.title}</li>)}</ul>
          <div className="manager-actions"><button disabled={busy} onClick={() => setEmptyTarget(null)}>{ui("取消")}</button><button className="danger" disabled={busy} onClick={() => void clearTrash()}>{busy ? ui("正在清空…") : ui("確認清空垃圾桶")}</button></div>
        </Modal>}
        {purge && <Modal title={ui("永久刪除作品確認")} onClose={()=>setPurge(null)} busy={busy} className="purge-confirm">
          {error&&<p className="error" role="alert">{error}</p>}<h3>{ui("永久刪除「")}{purge.project.title}」？</h3>
          <p>{purge.scope === "workspace" ? ui("將刪除 Ownsei 工作資料：轉錄、譯文、人工校對、快取、日誌及工作目錄內的匯出檔。無法還原；原始音檔保留。") : ui("這是外部專案目錄，只會永久移除垃圾桶登錄；目錄、原音及工作檔案全部保留，不會釋放磁碟空間。")}</p><code>{purge.path}</code>
          <div className="manager-actions"><button disabled={busy} onClick={() => setPurge(null)}>{ui("取消")}</button><button className="danger" disabled={busy} onClick={() => void change(() => api(`/projects/${purge.project.id}/purge`, "DELETE"))}>{ui("確認永久刪除")}</button></div>
        </Modal>}
        <div className="manager-list" inert={!!purge || !!emptyTarget}>
          {projects.length === 0 && (<p className="manager-empty">
              {query
                ? ui("沒有符合的作品。") : tab === "trash"
                ? ui("垃圾桶目前是空的。") : ui("還沒有作品。")}
            </p>)}
          {projects.map((p) => (<article className="manager-project" key={p.id}>
              <strong>{p.title}</strong>
              <small>
                {p.tracks}{ui("條音軌")}{p.trashed_at && ui(" · 刪除於 ") + (new Date(p.trashed_at * 1000).toLocaleString("zh-TW")) + ""}
              </small>
              <code>{p.path}</code>
              {editing === p.id ? (<form className="manager-rename" onSubmit={(e) => {
                    e.preventDefault();
                    if (title.trim() && !busy) {
                        void change(() => api(`/projects/${p.id}/settings`, "PATCH", {
                            title: title.trim(),
                        }));
                    }
                }}>
                  <label>{ui("新作品名稱")}<input autoFocus maxLength={200} value={title} onChange={(e) => setTitle(e.target.value)}/>
                  </label>
                  <button className="primary" disabled={busy || !title.trim()}>{ui("儲存名稱")}</button>
                  <button type="button" disabled={busy} onClick={() => setEditing("")}>{ui("取消改名")}</button>
                </form>) : deleting === p.id ? (<div className="manager-confirm">
                  <p>{ui("將「")}{p.title}{ui("」移至垃圾桶？之後可還原；執行中的任務須先取消。")}</p>
                  <button className="danger" disabled={busy} onClick={() => void change(() => api(`/projects/${p.id}`, "DELETE"))}>{ui("確認刪除")}</button>
                  <button disabled={busy} onClick={() => setDeleting("")}>{ui("取消刪除")}</button>
                </div>) : (<div className="manager-actions">
                  {tab === "active" ? (<>
                      <button disabled={busy} onClick={() => onOpen(p.id)}>{ui("開啟作品")}</button>
                      <button aria-label={ui("修改「") + (p.title) + ui("」名稱")} disabled={busy} onClick={() => {
                        setEditing(p.id);
                        setTitle(p.title);
                        setDeleting("");
                    }}>{ui("改名")}</button>
                      <button className="danger" aria-label={ui("刪除「") + (p.title) + "」"} disabled={busy} onClick={() => {
                        setDeleting(p.id);
                        setEditing("");
                    }}>{ui("刪除")}</button>
                    </>) : (<>
                    <button className="primary" aria-label={ui("還原「") + (p.title) + "」"} disabled={busy} onClick={() => void change(() => api(`/projects/${p.id}/restore`, "POST"))}>{ui("還原作品")}</button>
                    <button className="danger" disabled={busy} onClick={() => { setError(""); void api<{
                    scope: string;
                    path: string;
                }>(`/projects/${p.id}/purge`).then(info => setPurge({ project: p, ...info })).catch(e => setError(String(e))); }}>{ui("永久刪除")}</button>
                    </>)}
                </div>)}
            </article>))}
        </div>
      </Modal>);
}
