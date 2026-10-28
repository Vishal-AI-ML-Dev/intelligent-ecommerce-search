import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { HttpResult } from '../../shared/http/client';
import { useReturnsRequests } from './useReturnsRequests';

// The guard itself, independent of React ignoring updates to unmounted components: the value a
// caller receives for a cancelled request is `undefined`, and a cancelled 401 never reaches
// `onUnauthorized`. Every request here ignores its AbortSignal and resolves anyway.

function late<T>() {
  let resolve: (value: HttpResult<T>) => void = () => undefined;
  const signals: AbortSignal[] = [];
  const promise = new Promise<HttpResult<T>>((r) => {
    resolve = r;
  });
  const request = (signal: AbortSignal) => {
    signals.push(signal);
    return promise;
  };
  return { request, resolve, signals };
}

const UNAUTHORIZED: HttpResult<string> = { ok: false, error: { kind: 'http', status: 401 } };

describe('useReturnsRequests', () => {
  it('returns a current result and reports a current 401', async () => {
    const onUnauthorized = vi.fn();
    const { result } = renderHook(() => useReturnsRequests(onUnauthorized));
    expect(await result.current('a', async () => ({ ok: true, data: 'x' }))).toEqual({ ok: true, data: 'x' });
    expect(await result.current('a', async () => UNAUTHORIZED)).toBeUndefined();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it('drops a superseded response while the component stays mounted', async () => {
    const onUnauthorized = vi.fn();
    const { result } = renderHook(() => useReturnsRequests(onUnauthorized));
    const old = late<string>();
    const first = result.current('page', old.request);
    expect(await result.current('page', async () => ({ ok: true, data: 'new' }))).toEqual({ ok: true, data: 'new' });
    expect(old.signals[0]?.aborted).toBe(true);
    old.resolve({ ok: true, data: 'old' });
    expect(await first).toBeUndefined();
  });

  it('drops a superseded 401 without reporting it', async () => {
    const onUnauthorized = vi.fn();
    const { result } = renderHook(() => useReturnsRequests(onUnauthorized));
    const old = late<string>();
    const first = result.current('page', old.request);
    await result.current('page', async () => ({ ok: true, data: 'new' }));
    old.resolve(UNAUTHORIZED);
    expect(await first).toBeUndefined();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it('drops data and 401s that arrive after unmount (sign-out)', async () => {
    const onUnauthorized = vi.fn();
    const { result, unmount } = renderHook(() => useReturnsRequests(onUnauthorized));
    const data = late<string>();
    const denied = late<string>();
    const pendingData = result.current('page', data.request);
    const pendingDenied = result.current('detail', denied.request);
    unmount();
    expect(data.signals[0]?.aborted).toBe(true);
    expect(denied.signals[0]?.aborted).toBe(true);
    data.resolve({ ok: true, data: 'old' });
    denied.resolve(UNAUTHORIZED);
    expect(await pendingData).toBeUndefined();
    expect(await pendingDenied).toBeUndefined();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
