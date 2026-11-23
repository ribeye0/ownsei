import { t as ui } from "./i18n";
import { useEffect, useRef, useState } from "react";
import { Modal } from "./Modal";
import { api, type Project } from "./api";

type Props = {
  onClose: () => void;
  onChanged: () => Promise<void>;
  onOpen: (id: string) => void;
};
type EmptyTrashResult = {
  deleted: { id: string; scope: string }[];
  failed: { id: string; error: string }[];
};

export function ProjectManager({ onClose, onChanged, onOpen }: Props) {
  const [active, setActive] = useState<Project[]>([]);
  const [trash, setTrash] = useState<Project[]>([]);
  const [tab, setTab] = useState("active");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("newest");
  const [editing, setEditing] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [error, setError] = useState("");
  const [emptyResult, setEmptyResult] = useState<{
    deleted: number;
    failed: { title: string; error: string }[];
  } | null>(null);

  async function load() {
    const [projects, removed] = await Promise.all([
      api<Project[]>("/projects"),
      api<Project[]>("/projects?trashed=true"),
    ]);
    setActive(projects);
    setTrash(removed);
  }
  useEffect(() => {
    void load().catch((e) => setError(String(e)));
  }, []);

  async function change(action: () => Promise<unknown>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setEmptyResult(null);
    try {
      await action();
      setEditing("");
      await load();
      await onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      pending.current = false;
      setBusy(false);
    }
  }
  const query = search.trim().toLocaleLowerCase();
  const projects = (tab === "active" ? active : trash)
    .filter((p) => `${p.title} ${p.path}`.toLocaleLowerCase().includes(query))
    .sort((a, b) =>
      sort === "title"
        ? a.title.localeCompare(b.title, "zh-Hant", { numeric: true })
        : (b.trashed_at || b.created_at || 0) -
          (a.trashed_at || a.created_at || 0),
    );

  async function clearTrash() {
    const targets = [...trash];
    if (!targets.length) return;
    await change(async () => {
      const result = await api<EmptyTrashResult>(
        "/trash/purge",
        "DELETE",
        {
          project_ids: targets.map((p) => p.id),
        },
        { timeoutMs: 180000 },
      );
      setEmptyResult({
        deleted: result.deleted.length,
        failed: result.failed.map((f) => ({
          title: targets.find((p) => p.id === f.id)?.title || f.id,
          error: f.error,
        })),
      });
    });
  }
  return (
    <Modal
      title={ui("專案管理")}
      onClose={onClose}
      busy={busy}
      className="project-manager"
    >
      <div className="manager-tabs">
        <button
          aria-pressed={tab === "active"}
          disabled={busy}
          onClick={() => {
            setTab("active");
            setEditing("");
          }}
        >
          {ui("我的專案 (")}
          {active.length})
        </button>
        <button
          aria-pressed={tab === "trash"}
          disabled={busy}
          onClick={() => {
            setTab("trash");
            setEditing("");
          }}
        >
          {ui("垃圾桶 (")}
          {trash.length})
        </button>
      </div>
      <p>
        {tab === "active"
          ? ui("刪除後移至垃圾桶，可隨時還原。")
          : ui("永久刪除專案，原始音檔保留。")}
      </p>
      <div className="manager-tools">
        <input
          autoFocus
          aria-label={ui("搜尋專案名稱或路徑")}
          placeholder={ui("搜尋專案名稱或路徑")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label>
          {ui("排序")}
          <select value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="newest">{ui("最新在前")}</option>
            <option value="title">{ui("名稱順序")}</option>
          </select>
        </label>
        {tab === "trash" && (
          <button
            className="danger"
            disabled={busy || !trash.length}
            onClick={() => void clearTrash()}
          >
            {ui("清空垃圾桶")}
          </button>
        )}
      </div>
      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}
      {emptyResult && (
        <div className="trash-result" role="status">
          <p>
            {ui("已永久移除 ")}
            {emptyResult.deleted}
            {ui(" 個專案；保留 ")}
            {emptyResult.failed.length}
            {ui(" 部未能移除的專案")}
          </p>
          {emptyResult.failed.length > 0 && (
            <ul>
              {emptyResult.failed.map((f, i) => (
                <li key={i}>
                  <strong>{f.title}</strong>：{f.error}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="manager-list">
        {projects.length === 0 && (
          <p className="manager-empty">
            {query
              ? ui("沒有符合的專案。")
              : tab === "trash"
                ? ui("垃圾桶目前是空的。")
                : ui("還沒有專案。")}
          </p>
        )}
        {projects.map((p) => (
          <article className="manager-project" key={p.id}>
            <strong>{p.title}</strong>
            <small>
              {p.tracks}
              {ui("條音軌")}
              {p.trashed_at
                ? ui(" · 刪除於 ") +
                  new Date(p.trashed_at * 1000).toLocaleString()
                : ""}
            </small>
            <code>{p.path}</code>
            {editing === p.id ? (
              <form
                className="manager-rename"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (title.trim() && !busy)
                    void change(() =>
                      api(`/projects/${p.id}/settings`, "PATCH", {
                        title: title.trim(),
                      }),
                    );
                }}
              >
                <label>
                  {ui("新專案名稱")}
                  <input
                    autoFocus
                    maxLength={200}
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                </label>
                <button className="primary" disabled={busy || !title.trim()}>
                  {ui("儲存名稱")}
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setEditing("")}
                >
                  {ui("取消改名")}
                </button>
              </form>
            ) : (
              <div className="manager-actions">
                {tab === "active" ? (
                  <>
                    <button disabled={busy} onClick={() => onOpen(p.id)}>
                      {ui("開啟專案")}
                    </button>
                    <button
                      aria-label={ui("修改「") + p.title + ui("」名稱")}
                      disabled={busy}
                      onClick={() => {
                        setEditing(p.id);
                        setTitle(p.title);
                      }}
                    >
                      {ui("改名")}
                    </button>
                    <button
                      className="danger"
                      aria-label={ui("刪除「") + p.title + "」"}
                      disabled={busy}
                      onClick={() =>
                        void change(() => api(`/projects/${p.id}`, "DELETE"))
                      }
                    >
                      {ui("刪除")}
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      className="primary"
                      aria-label={ui("還原「") + p.title + "」"}
                      disabled={busy}
                      onClick={() =>
                        void change(() =>
                          api(`/projects/${p.id}/restore`, "POST"),
                        )
                      }
                    >
                      {ui("還原專案")}
                    </button>
                    <button
                      className="danger"
                      aria-label={ui("永久刪除「") + p.title + "」"}
                      disabled={busy}
                      onClick={() =>
                        void change(() =>
                          api(`/projects/${p.id}/purge`, "DELETE", undefined, {
                            timeoutMs: 180000,
                          }),
                        )
                      }
                    >
                      {ui("永久刪除")}
                    </button>
                  </>
                )}
              </div>
            )}
          </article>
        ))}
      </div>
    </Modal>
  );
}
