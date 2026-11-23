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
    def test_interlocutor_role_and_gender_are_separate_from_the_female_agent(self):
        for name, expected in [('Александр', 'male'), ('Анна', 'female'), ('Саша', 'neutral'), ('user312', 'neutral')]:
            settings = {'accounts': [{'telegram_id': 11, 'name': name, 'bot_access': False}]}
            text = runner.persona(settings, '11')
            self.assertIn('"form_of_address": "' + expected + '"', text)
            self.assertIn('"role": "user"', text)
            self.assertIn('Твой женский род относится только к тебе', text)
        settings['accounts'][0].update(bot_access=True, address_as='Саша', address_gender='male')
        text = runner.persona(settings, '11')
        self.assertIn('"role": "administrator"', text)
        self.assertIn('"form_of_address": "male"', text)
        self.assertIn('Саша', text)

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

    def test_subscription_links_are_escaped_and_code_is_not_linkified(self):
        value=bot.telegram_html('[Моя <подписка>](https://example.org/s/token?a=1&b=2)')
        self.assertEqual(value,'<a href="https://example.org/s/token?a=1&amp;b=2">Моя &lt;подписка&gt;</a>')
        for unsafe in ('[link](javascript:alert(1))','[link](https://user:password@example.org/)'):
            self.assertNotIn('<a ',bot.telegram_html(unsafe))
        self.assertIn('<code>[link](https://example.org/)</code>',bot.telegram_html('`[link](https://example.org/)`'))

    def test_long_hyperlinks_reopen_with_valid_anchor_attributes(self):
        label='🙂' * 2400
        chunks=bot.telegram_chunks('['+label+'](https://example.org/s/token?a=1&b=2)')
        self.assertGreater(len(chunks),1)
        visible=''
        for chunk in chunks:
            self.assertIn('<a href="https://example.org/s/token?a=1&amp;b=2">',chunk)
            self.assertNotIn('</a href',chunk)
            parser=Visible(); parser.feed(chunk)
            self.assertEqual(parser.stack,[])
            self.assertLessEqual(len(parser.text.encode('utf-16-le'))//2,3500)
            visible+=parser.text
        self.assertEqual(visible,label)

    def test_nodera_persona_tone_and_permissions_are_in_every_request(self):
        value = runner.persona({'server_name': 'test-host', 'assistant_style': 'technical', 'assistant_instructions': 'Use exact units', 'api_key': 'secret-key'})
        self.assertIn('девушка', value)
        self.assertIn('AI-ассистент', value)
        self.assertIn('женском роде', value)
        self.assertIn('test-host', value)
        self.assertIn('Use exact units', value)
        self.assertIn('vps_service_action', value)
        self.assertIn('Не подтверждай действия за пользователя', value)
        self.assertNotIn('secret-key', value)
