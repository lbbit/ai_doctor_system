import { Navigate, Route, Routes } from 'react-router-dom';
import Layout from './components/Layout';
import ArchivePage from './pages/ArchivePage';
import ChatPage from './pages/ChatPage';
import DocumentDetailPage from './pages/DocumentDetailPage';
import LoginPage from './pages/LoginPage';
import SettingsPage from './pages/SettingsPage';
import SubjectsPage from './pages/SubjectsPage';
import TimelinePage from './pages/TimelinePage';
import { useAuthStore } from './stores/authStore';

/** 受保护路由：未登录 → /login */
function RequireAuth({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/app"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/app/timeline" replace />} />
        <Route path="timeline" element={<TimelinePage />} />
        <Route path="archive" element={<ArchivePage />} />
        <Route path="archive/:documentId" element={<DocumentDetailPage />} />
        {/*
          问答页必须只声明一条可选参数路由。
          若拆成 `chat` 与 `chat/:conversationId` 两条，进入 /app/chat 后页面会
          navigate 到 /app/chat/:id，React Router 会卸载旧 Route 并挂载新 Route，
          旧实例的异步 setState 仍会写入，导致同一问答渲染两遍（实测缺陷）。
        */}
        <Route path="chat/:conversationId?" element={<ChatPage />} />
        <Route path="subjects" element={<SubjectsPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/app" replace />} />
    </Routes>
  );
}
