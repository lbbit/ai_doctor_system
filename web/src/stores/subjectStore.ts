import { create } from 'zustand';
import { subjectsApi } from '../api/subjects';
import type { Subject } from '../types/api';

const STORAGE_KEY = 'aidoctor_subject';

interface PendingSwitch {
  from: Subject;
  to: Subject;
}

interface SubjectState {
  subjects: Subject[];
  current: Subject | null;
  loading: boolean;
  /** E-8：处于活跃会话时开启切换守卫 */
  guardActive: boolean;
  pendingSwitch: PendingSwitch | null;

  loadSubjects: () => Promise<void>;
  selectSubject: (id: string) => 'ok' | 'pending' | 'missing';
  commitPendingSwitch: () => void;
  cancelPendingSwitch: () => void;
  setGuardActive: (active: boolean) => void;
  applyRemoteSubjects: (subjects: Subject[], lastSubjectId: string | null) => void;
}

function persist(id: string | null): void {
  if (id) localStorage.setItem(STORAGE_KEY, id);
  else localStorage.removeItem(STORAGE_KEY);
}

export const useSubjectStore = create<SubjectState>((set, get) => ({
  subjects: [],
  current: null,
  loading: false,
  guardActive: false,
  pendingSwitch: null,

  async loadSubjects() {
    set({ loading: true });
    try {
      const list = await subjectsApi.list();
      const savedId = localStorage.getItem(STORAGE_KEY);
      const current =
        list.find((s) => s.id === savedId) || list.find((s) => s.isDefault) || list[0] || null;
      persist(current?.id ?? null);
      set({ subjects: list, current, loading: false });
      if (current) {
        void subjectsApi.setLastSelected(current.id).catch(() => undefined);
      }
    } catch {
      set({ loading: false });
    }
  },

  selectSubject(id) {
    const { subjects, current, guardActive } = get();
    const next = subjects.find((s) => s.id === id);
    if (!next) return 'missing';
    if (current && current.id === next.id) return 'ok';
    if (guardActive && current) {
      // E-8：会话中途切换需确认弹窗阻断
      set({ pendingSwitch: { from: current, to: next } });
      return 'pending';
    }
    persist(next.id);
    set({ current: next });
    void subjectsApi.setLastSelected(next.id).catch(() => undefined);
    return 'ok';
  },

  commitPendingSwitch() {
    const { pendingSwitch } = get();
    if (!pendingSwitch) return;
    persist(pendingSwitch.to.id);
    set({ current: pendingSwitch.to, pendingSwitch: null });
    void subjectsApi.setLastSelected(pendingSwitch.to.id).catch(() => undefined);
  },

  cancelPendingSwitch() {
    set({ pendingSwitch: null });
  },

  setGuardActive(active) {
    set({ guardActive: active });
  },

  applyRemoteSubjects(subjects, lastSubjectId) {
    const savedId = localStorage.getItem(STORAGE_KEY);
    const current =
      subjects.find((s) => s.id === savedId) ||
      subjects.find((s) => s.id === lastSubjectId) ||
      subjects.find((s) => s.isDefault) ||
      subjects[0] ||
      null;
    persist(current?.id ?? null);
    set({ subjects, current });
  },
}));
