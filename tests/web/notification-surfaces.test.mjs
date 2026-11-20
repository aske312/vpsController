import assert from 'node:assert/strict';
import test from 'node:test';
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const root = fileURLToPath(new URL('../../', import.meta.url));
function files(path) {
  return readdirSync(path, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
    ? files(join(path, entry.name)) : entry.name.endsWith('.tsx') ? [join(path, entry.name)] : []);
}

test('all screens and dialogs route standalone alerts and legacy notice boxes to notifications', () => {
  const violations = [];
  const legacy = new Set(['mcpNotice', 'mcpRuntimeUpdate', 'mihomoMessage', 'mihomoConnectionStatusNote', 'routeLibraryCallout', 'routeUsageNotice', 'networkNotice', 'relayFormValidation']);
  for (const path of [...files(join(root, 'src')), ...files(join(root, 'app'))]) {
    const name = relative(root, path).replaceAll('\\', '/');
    // The center is the destination. Chat replies also show their own action
    // context, while the global notification remains visible on every screen.
    if (name === 'src/shared/notifications/notification-center.tsx' || name === 'src/features/assistant/confirmation-queue.tsx') continue;
    const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    function visit(node) {
      if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && /^[a-z]/.test(node.tagName.getText(source))) {
        for (const attribute of node.attributes.properties) {
          if (!ts.isJsxAttribute(attribute) || !attribute.initializer) continue;
          const key = attribute.name.getText(source), value = ts.isStringLiteral(attribute.initializer) ? attribute.initializer.text : attribute.initializer.getText(source);
          let alertRole = false;
          function checkRole(value) {
            if (ts.isStringLiteral(value) && value.text === 'alert') alertRole = true;
            ts.forEachChild(value, checkRole);
          }
          if (key === 'role') checkRole(attribute.initializer);
          if (alertRole || key === 'className' && value.split(/\s+/).some(item => legacy.has(item))) {
            violations.push(`${name}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1} ${key}=${value}`);
          }
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(source);
  }
  assert.deepEqual(violations, [], 'render NotificationNotice instead of an inline alert box');
});
