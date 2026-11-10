from html.parser import HTMLParser
import unittest

from tests.api.support import load_module

bot = load_module('assistant_format_bot', 'protocol-images/nodera-bot/bot.py')
runner = load_module('assistant_format_agent', 'protocol-images/ai-agent/runner.py')


class Visible(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack = []; self.text = ''
    def handle_starttag(self, tag, attrs):
        self.stack.append(tag)
    def handle_endtag(self, tag):
        assert self.stack.pop() == tag
    def handle_data(self, data):
        self.text += data


class AssistantFormatTests(unittest.TestCase):
    def test_telegram_escapes_symbols_and_keeps_code_literal(self):
        value = bot.telegram_html('**Нодера**: <script>&\n```py\nprint("<>&_*")\n```')
        self.assertIn('<b>Нодера</b>', value)
        self.assertNotIn('<script>', value)
        parser = Visible(); parser.feed(value)
        self.assertIn('print("<>&_*")', parser.text)
        self.assertEqual(parser.stack, [])

    def test_long_unicode_chunks_are_balanced_and_lossless(self):
        raw = '🙂<&_ ' * 1600
        chunks = bot.telegram_chunks('```text\n' + raw + '\n```')
        text = ''
        for chunk in chunks:
            parser = Visible(); parser.feed(chunk)
            self.assertEqual(parser.stack, [])
            self.assertLessEqual(len(parser.text.encode('utf-16-le')) // 2, 3500)
            text += parser.text
        self.assertEqual(text, raw + '\n')

    def test_nodera_persona_tone_and_permissions_are_in_every_request(self):
        value = runner.persona({'server_name': 'test-host', 'assistant_style': 'technical', 'assistant_instructions': 'Use exact units', 'api_key': 'secret-key'})
        self.assertIn('девушка', value)
        self.assertIn('женском роде', value)
        self.assertIn('test-host', value)
        self.assertIn('Use exact units', value)
        self.assertIn('только чтение', value)
        self.assertNotIn('secret-key', value)
