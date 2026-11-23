import { t as ui } from "./i18n";
import { Icon } from "./Icon";
import type { Project } from "./api";

type SidebarProps = {
  projects: Project[];
  pid: string;
  theme: string;
  projectSearch: string;
  busy: boolean;
  inert: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onSearch: (value: string) => void;
  onDialog: (value: string) => void;
};
export function Sidebar({
  projects,
  pid,
  theme,
  projectSearch,
  busy,
  inert,
  onNew,
  onSelect,
  onDelete,
  onSearch,
  onDialog,
}: SidebarProps) {
  return (
    <aside inert={inert}>
      <div className="brand">
        <img
          className="brandmark"
          src={theme === "mocha" ? "/ownsei-logo-dark.png" : "/ownsei-logo.png"}
          alt=""
        />
        <b>Ownsei</b>
        <button
          className="help-button"
          aria-label={ui("快捷鍵說明")}
          title={ui("快捷鍵說明")}
          onClick={() => onDialog("shortcuts")}
        >
          ?
        </button>
      </div>
      <div className="section-label">
        <span>
          {ui("專案")}
          <small>{projects.length}</small>
        </span>
        <button className="icon" aria-label={ui("建立專案")} onClick={onNew}>
          <Icon name="plus" />
        </button>
      </div>
      <input
        className="project-search"
        aria-label={ui("搜尋側欄專案")}
        placeholder={ui("篩選專案…")}
        value={projectSearch}
        onChange={(e) => onSearch(e.target.value)}
      />
      <nav aria-label={ui("專案列表")}>
        {projects
          .filter((p) =>
            p.title.toLowerCase().includes(projectSearch.toLowerCase()),
          )
          .map((p) => (
            <div
              className={"project-row " + (p.id === pid ? "current" : "")}
              key={p.id}
            >
              <button
                className="project"
                aria-label={p.title}
                aria-current={p.id === pid ? "true" : undefined}
                title={p.title}
                onClick={() => onSelect(p.id)}
              >
                <Icon name="folder" />
                <span>
                  {p.title}
                  <small>
                    {p.tracks}
                    {ui(" 條音軌")}
                  </small>
                </span>
              </button>
              <button
                className="project-delete"
                disabled={busy}
                aria-label={ui("刪除「") + p.title + "」"}
                title={ui("刪除專案")}
                onClick={() => onDelete(p.id)}
              >
                ×
              </button>
            </div>
          ))}
      </nav>
      <button
        className="manage-projects"
        aria-label={ui("專案管理")}
        title={ui("專案管理")}
        onClick={() => onDialog("manage")}
      >
        <Icon name="folder" />
        <span>{ui("專案管理")}</span>
      </button>
      <button
        className="manage-projects"
        disabled={busy}
        onClick={() => onDialog("global")}
      >
        <Icon name="book" />
        {ui("全局術語表")}
      </button>
      <button className="manage-projects" onClick={() => onDialog("models")}>
        <Icon name="models" />
        {ui("模型管理")}
      </button>
      <button
        className="manage-projects"
        onClick={() => onDialog("preferences")}
      >
        <Icon name="settings" />
        {ui("介面設定")}
      </button>
      <div className="sidebar-foot">
        <span className="dot" />
        {ui("本機工作空間")}
        <small>
          {ui("專案資料自動保留")}
          <br />
          {ui("日文原文 → 中文字幕")}
          <br />
          {ui("Vulkan · 分階段推論")}
        </small>
      </div>
    </aside>
  );
}
