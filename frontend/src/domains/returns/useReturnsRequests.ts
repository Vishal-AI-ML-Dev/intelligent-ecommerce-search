import { useCallback, useEffect, useRef } from 'react';
import { type HttpResult, RequestAborted } from '../../shared/http/client';
import { isUnauthorized } from './messages';

/**
 * Runs `/returns` requests for one signed-in component.
 *
 * - Every request is aborted when the component unmounts (sign-out or 401 unmounts the whole
 *   signed-in view), and a starting request with the same key aborts the previous one.
 * - A response that arrives after its request was aborted is dropped (`undefined`), so it cannot
 *   repopulate a signed-out session or overwrite newer data.
 * - A 401 calls `onUnauthorized` and is dropped too.
 */
export function useReturnsRequests(onUnauthorized: () => void) {
  const controllers = useRef(new Map<string, AbortController>());
  const unauthorized = useRef(onUnauthorized);
  unauthorized.current = onUnauthorized;

  useEffect(() => {
    const active = controllers.current;
    return () => {
      for (const controller of active.values()) {
        controller.abort();
      }
      active.clear();
    };
  }, []);

  return useCallback(
    async <T>(
      key: string,
      request: (signal: AbortSignal) => Promise<HttpResult<T>>,
    ): Promise<HttpResult<T> | undefined> => {
      controllers.current.get(key)?.abort();
      const controller = new AbortController();
      controllers.current.set(key, controller);
      try {
        const result = await request(controller.signal);
        if (controller.signal.aborted) {
          return undefined;
        }
        if (!result.ok && isUnauthorized(result.error)) {
          unauthorized.current();
          return undefined;
        }
        return result;
      } catch (error) {
        if (error instanceof RequestAborted || controller.signal.aborted) {
          return undefined;
        }
        throw error;
      } finally {
        if (controllers.current.get(key) === controller) {
          controllers.current.delete(key);
        }
      }
    },
    [],
  );
}
