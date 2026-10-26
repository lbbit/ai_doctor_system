import { create } from 'zustand';
import { authApi } from '../api/auth';
import { tokenStore } from '../api/client';
import type { Account } from '../types/api';

interface AuthState {
  token: string | null;
  account: Account | null;
  status: 'idle' | 'loading' | 'authenticated' | 'anonymous';
  error: string | null;

  register: (payload: {
    email: string;
    password: string;
    displayName?: string;
  }) => Promise<void>;
  login: (payload: { email: string; password: string }) => Promise<void>;
  logout: () => void;
  forceLogout: () => void;
  bootstrap: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  token: tokenStore.get(),
  account: null,
  status: 'idle',
  error: null,

  async register(payload) {
    set({ status: 'loading', error: null });
    try {
      const res = await authApi.register(payload);
      tokenStore.set(res.token);
      set({ token: res.token, account: res.account, status: 'authenticated' });
    } catch (err) {
      set({ status: 'anonymous', error: (err as Error).message });
      throw err;
    }
  },

  async login(payload) {
    set({ status: 'loading', error: null });
    try {
      const res = await authApi.login(payload);
      tokenStore.set(res.token);
      set({ token: res.token, account: res.account, status: 'authenticated' });
    } catch (err) {
      set({ status: 'anonymous', error: (err as Error).message });
      throw err;
    }
  },

  logout() {
    tokenStore.clear();
    set({ token: null, account: null, status: 'anonymous' });
  },

  forceLogout() {
    tokenStore.clear();
    set({ token: null, account: null, status: 'anonymous', error: '登录已失效' });
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
  },

  async bootstrap() {
    const token = tokenStore.get();
    if (!token) {
      set({ status: 'anonymous', token: null });
      return;
    }
    set({ status: 'loading' });
    try {
      const me = await authApi.me();
      set({
        token,
        account: me.account,
        status: 'authenticated',
      });
    } catch {
      tokenStore.clear();
      set({ token: null, account: null, status: 'anonymous' });
    }
  },
}));
