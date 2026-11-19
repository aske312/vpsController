import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import ts from 'typescript';
import * as storeApi from '../../src/shared/notifications/store.ts';
import * as presentation from '../../src/shared/notifications/operation-presentation.ts';

// Exercise the real notification hooks with the component's controlled effects.
export function notificationFixture(hooks) {
  const store = storeApi.createNotificationStore();
  const actual = createRequire(import.meta.url), api = {};
  const source = readFileSync(new URL('../../src/shared/notifications/notification-center.tsx', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText;
  new Function('require', 'exports', output + '\nexports.renderCard = NotificationCard;')(name => name === 'react'
    ? { ...hooks, createContext: () => ({}), useContext: () => store, useMemo: fn => fn() }
    : name === './store' ? storeApi
    : name === './operation-presentation' ? presentation : name === './notification-target' ? {} : actual(name), api);
  return { api, store };
}
