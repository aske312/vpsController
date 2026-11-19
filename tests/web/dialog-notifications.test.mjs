import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

test('profile dialogs allow notification actions and retain their keyboard focus boundary', () => {
  const previousDocument = globalThis.document, previousHTMLElement = globalThis.HTMLElement;
  const effects = [], listeners = {};
  class Element {
    children = [];
    tabIndex = 0;
    isConnected = true;
    contains(target) { return this === target || this.children.some(child => child.contains(target)); }
    querySelectorAll() { return this.children; }
    querySelector() { return null; }
    matches() { return false; }
    getClientRects() { return [{}]; }
    focus() { document.activeElement = this; }
  }
  const previous = new Element(), dialog = new Element(), field = new Element(), dock = new Element(), action = new Element();
  dialog.children = [field]; dock.children = [action];
  let native = null, closed = 0, refs = 0;
  globalThis.HTMLElement = Element;
  globalThis.document = {
    activeElement: previous,
    querySelector: selector => selector === '.gateNotificationDock' ? dock : native,
    addEventListener: (type, handler) => { listeners[type] = handler; },
    removeEventListener: type => { delete listeners[type]; },
  };
  const api = {};
  const source = readFileSync(new URL('../../src/shared/components/use-dialog-focus.ts', import.meta.url), 'utf8');
  const output = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  new Function('require', 'exports', output)(() => ({
    useRef: value => ({ current: refs++ === 0 ? dialog : value }),
    useEffect: run => effects.push(run),
  }), api);
  try {
    api.useDialogFocus(true, () => closed++);
    const cleanups = effects.map(run => run());
    assert.equal(document.activeElement, field);
    action.focus(); listeners.focusin({ target: action });
    assert.equal(document.activeElement, action, 'notification buttons keep focus inside an open profile editor');
    listeners.keydown({ key: 'Escape', target: action });
    assert.equal(closed, 0, 'Escape in the notification does not close an unsaved profile');
    let prevented = false;
    listeners.keydown({ key: 'Tab', target: action, preventDefault() { prevented = true; } });
    assert.ok(prevented);
    assert.equal(document.activeElement, field);
    const outside = new Element(); outside.focus(); listeners.focusin({ target: outside });
    assert.equal(document.activeElement, field, 'unrelated page controls remain outside the focus boundary');
    native = new Element(); outside.focus(); listeners.focusin({ target: outside });
    assert.equal(document.activeElement, outside, 'a native dialog owns its own focus boundary');
    cleanups.forEach(cleanup => cleanup?.());
    assert.equal(document.activeElement, previous);
    assert.deepEqual(listeners, {});
  } finally {
    if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument;
    if (previousHTMLElement === undefined) delete globalThis.HTMLElement; else globalThis.HTMLElement = previousHTMLElement;
  }
});
