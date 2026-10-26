import { NavLink, Outlet, useNavigate } from 'react-router-dom';
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import SubjectSwitcher from './SubjectSwitcher';
import { subjectsApi } from '../api/subjects';
import { useAuthStore } from '../stores/authStore';
import { useSubjectStore } from '../stores/subjectStore';

/** 主应用布局：顶部常驻 Subject 切换大 chip + 免责声明 + 导航（§6.1）。 */
export default function Layout() {
  const navigate = useNavigate();
  const token = useAuthStore((s) => s.token);
  const bootstrap = useAuthStore((s) => s.bootstrap);
  const loadSubjects = useSubjectStore((s) => s.loadSubjects);

  useEffect(() => {
    if (!token) {
      navigate('/login', { replace: true });
      return;
    }
    void bootstrap().then(() => loadSubjects());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 订阅 401 事件（兜底，main.tsx 已全局监听）
  useEffect(() => {
    const onUnauth = () => navigate('/login', { replace: true });
    window.addEventListener('auth:unauthorized', onUnauth);
    return () => window.removeEventListener('auth:unauthorized', onUnauth);
  }, [navigate]);

  const current = useSubjectStore((s) => s.current);
  const statsQuery = useQuery({
    queryKey: ['subject-stats', current?.id],
    queryFn: () => subjectsApi.stats(current!.id),
    enabled: Boolean(current?.id),
    staleTime: 30_000,
  });

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-header__left">
          <span className="app-logo">AI 私人医生</span>
          <SubjectSwitcher />
        </div>
        <nav className="app-nav">
          <NavLink to="/app/timeline" className={({ isActive }) => (isActive ? 'active' : '')}>
            时间线
          </NavLink>
          <NavLink to="/app/archive" className={({ isActive }) => (isActive ? 'active' : '')}>
            档案
          </NavLink>
          <NavLink to="/app/chat" className={({ isActive }) => (isActive ? 'active' : '')}>
            问诊
          </NavLink>
          <NavLink to="/app/subjects" className={({ isActive }) => (isActive ? 'active' : '')}>
            成员
          </NavLink>
          <NavLink to="/app/settings" className={({ isActive }) => (isActive ? 'active' : '')}>
            设置
          </NavLink>
        </nav>
      </header>
      {current && (
        <div className="app-subject-bar">
          <span>
            当前档案：<strong>{current.name}</strong>（{current.relation}）
          </span>
          <span className="app-subject-bar__stats">
            {statsQuery.data
              ? `事件 ${statsQuery.data.eventCount} · 指标 ${statsQuery.data.observationCount} · 文档 ${statsQuery.data.documentCount}`
              : ''}
          </span>
        </div>
      )}
      <main className="app-main">
        <Outlet />
      </main>
      <footer className="app-footer">
        免责声明：本系统提供健康参考与就医建议，不构成诊断或处方；紧急情况请拨打 120。
      </footer>
    </div>
  );
}
