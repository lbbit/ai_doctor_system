/** axios 实例：统一响应包裹 {code,message,data,requestId} 解包（§8.1）。 */

import axios, { AxiosError, AxiosInstance } from 'axios';
import { messageForCode } from './errors';

export interface ApiEnvelope<T> {
  code: number | string;
  message: string;
  data: T;
  requestId: string;
}

const TOKEN_KEY = 'aidoctor_token';

export const tokenStore = {
  get(): string | null {
    return localStorage.getItem(TOKEN_KEY);
  },
  set(token: string): void {
    localStorage.setItem(TOKEN_KEY, token);
  },
  clear(): void {
    localStorage.removeItem(TOKEN_KEY);
  },
};

/** 带 envelope 解包的响应（data 已取出）。 */
export interface Unwrapped<T> {
  data: T;
  requestId: string;
}

export class ApiError extends Error {
  code: string;
  httpStatus: number;
  requestId?: string;
  data?: unknown;

  constructor(
    code: string,
    message: string,
    httpStatus = 400,
    requestId?: string,
    data?: unknown,
  ) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.httpStatus = httpStatus;
    this.requestId = requestId;
    this.data = data;
  }
}

function statusToCode(status: number): string {
  if (status === 401) return 'UNAUTHENTICATED';
  if (status === 403) return 'FORBIDDEN';
  if (status === 404) return 'NOT_FOUND';
  if (status === 409) return 'CONFLICT';
  if (status === 413) return 'FILE_TOO_LARGE';
  if (status === 415) return 'UNSUPPORTED_MEDIA_TYPE';
  if (status === 422) return 'VALIDATION_ERROR';
  if (status === 429) return 'RATE_LIMITED';
  if (status >= 500) return 'INTERNAL';
  return 'BAD_REQUEST';
}

const client: AxiosInstance = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api',
  timeout: 60_000,
});

client.interceptors.request.use((config) => {
  const token = tokenStore.get();
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

client.interceptors.response.use(
  (response) => {
    const body = response.data as ApiEnvelope<unknown> | undefined;
    if (
      body &&
      typeof body === 'object' &&
      'code' in body &&
      'data' in body &&
      'requestId' in body
    ) {
      if (body.code === 0) {
        return {
          ...response,
          data: body.data,
          requestId: body.requestId,
        } as typeof response & { requestId: string };
      }
      throw new ApiError(
        String(body.code),
        body.message,
        response.status,
        body.requestId,
        body.data,
      );
    }
    return response;
  },
  (error: AxiosError<Partial<ApiEnvelope<unknown>> & { message?: string }>) => {
    if (error.response) {
      const body = error.response.data;
      const code = body?.code ? String(body.code) : statusToCode(error.response.status);
      const message =
        body?.message || messageForCode(code, '请求失败');
      if (error.response.status === 401) {
        tokenStore.clear();
        window.dispatchEvent(new CustomEvent('auth:unauthorized'));
      }
      throw new ApiError(code, message, error.response.status, body?.requestId, body?.data);
    }
    throw new ApiError('NETWORK_ERROR', messageForCode('NETWORK_ERROR'), 0);
  },
);

export default client;
