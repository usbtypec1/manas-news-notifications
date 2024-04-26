import contextlib
import datetime
import enum
import itertools
import operator
import pathlib
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, TypeAlias

import httpx
from bs4 import BeautifulSoup, Tag

TELEGRAM_BOT_TOKEN: Final[str] = 'token'
CHAT_USERNAME: Final[int] = 0

DATABASE_FILE_PATH = pathlib.Path(__file__).parent / 'database.db'


class Locale(enum.StrEnum):
    KG = enum.auto()
    RU = enum.auto()
    EN = enum.auto()
    TR = enum.auto()


LOCALE_TO_NEWS_TRANSLATION: Final[dict[Locale, str]] = {
    Locale.RU: '🇷🇺 Новости',
    Locale.KG: '🇰🇬 Жаңылыктар',
    Locale.EN: '🇬🇧 News',
    Locale.TR: '🇹🇷 Haberler',
}


@dataclass(frozen=True, slots=True)
class NewsArticle:
    id: int
    locale: Locale
    title: str
    date: datetime.date

    @property
    def url(self) -> str:
        return f'https://manas.edu.kg/{self.locale}/news/{self.id}'


class Database:

    def __init__(self, connection: sqlite3.Connection):
        self.__connection = connection

    def init(self) -> None:
        statement = '''
        CREATE TABLE IF NOT EXISTS news_articles (
            id INTEGER NOT NULL,
            locale TEXT NOT NULL,
            PRIMARY KEY (id, locale)
        );
        '''
        with self.__connection:
            self.__connection.execute(statement)

    def insert_article(self, news_articles: Iterable[NewsArticle]) -> None:
        statement = '''
        INSERT INTO news_articles (id, locale)
        VALUES (?, ?)
        ON CONFLICT (id, locale) DO NOTHING;
        '''
        args = [
            (news_article.id, news_article.locale)
            for news_article in news_articles
        ]
        with self.__connection:
            cursor = self.__connection.cursor()
            with contextlib.closing(cursor):
                cursor.executemany(statement, args)

    def get_article_ids(self, locale: Locale) -> set[int]:
        statement = 'SELECT id FROM news_articles WHERE locale = ?;'
        with self.__connection:
            cursor = self.__connection.cursor()
            with contextlib.closing(cursor):
                cursor.execute(statement, (locale,))
                result = cursor.fetchall()
        return {row[0] for row in result}


class NewsService:

    def __init__(self, locale: Locale, http_client: httpx.Client):
        self.__locale = locale
        self.__http_client = http_client

    def get_news(self) -> list[NewsArticle]:
        url = f'https://manas.edu.kg/{self.__locale}'
        response = self.__http_client.get(url)

        soup = BeautifulSoup(response.text, 'lxml')

        news_article_tags = soup.find_all(
            'article',
            attrs={'class': 'post-news'},
        )

        news_articles: list[NewsArticle] = []
        for news_article in news_article_tags:
            article_body = news_article.find(
                'div',
                attrs={'class': 'post-news-body'},
            )

            anchor: Tag | None = article_body.find('a')

            if anchor is None:
                continue

            short_link: str = anchor.get('href')

            if short_link is None:
                continue

            if 'news/' not in short_link:
                continue

            news_id = int(short_link.split('/')[-1])
            title = anchor.text.strip()

            article_date = datetime.datetime.strptime(
                article_body.find_all('span')[-1].text.strip(),
                '%d.%m.%Y',
            )

            news_article = NewsArticle(
                id=news_id,
                locale=self.__locale,
                title=title,
                date=article_date,
            )
            news_articles.append(news_article)

        return news_articles


class TelegramBot:

    def __init__(self, token: str, http_client: httpx.Client):
        self.__token = token
        self.__base_url = f'https://api.telegram.org/bot{self.__token}'
        self.__http_client = http_client

    def send_message(self, chat_id: int | str, text: str):
        url = f'{self.__base_url}/sendMessage'
        request_data = {
            'text': text,
            'chat_id': chat_id,
            'parse_mode': 'HTML',
            'disable_web_preview': True,
        }
        response = self.__http_client.post(url=url, json=request_data)
        print(response.text)


get_locale = operator.attrgetter('locale')
get_date = operator.attrgetter('date')

NewsArticles: TypeAlias = Iterable[NewsArticle]
NewsArticlesGroupedByLocale: TypeAlias = tuple[Locale, NewsArticles]
NewsArticlesGroupedByDate: TypeAlias = tuple[datetime.date, NewsArticles]


def render_news_articles(news_articles: NewsArticles) -> str:
    locale_and_news_articles: Iterable[NewsArticlesGroupedByLocale] = (
        itertools.groupby(news_articles, key=get_locale)
    )

    lines: list[str] = []
    for locale, news_articles_grouped_by_locale in locale_and_news_articles:

        news_articles_grouped_by_locale = sorted(
            news_articles_grouped_by_locale,
            key=get_date,
            reverse=True,
        )

        news_translation = LOCALE_TO_NEWS_TRANSLATION[locale]

        lines.append(f'<b>{news_translation}</b>')

        date_and_news_articles: NewsArticlesGroupedByDate = (
            itertools.groupby(news_articles_grouped_by_locale, key=get_date)
        )

        for date, news_articles_grouped_by_date in date_and_news_articles:

            lines.append(f'\n<b>{date:%d.%m.%Y}</b>')

            for news_article in news_articles_grouped_by_date:
                lines.append(
                    f'• <a href="{news_article.url}">{news_article.title}</a>'
                )

        lines.append('\n')

    return '\n'.join(lines)


def main() -> None:
    with (
        sqlite3.connect(DATABASE_FILE_PATH) as database_connection,
        httpx.Client() as news_http_client,
        httpx.Client() as telegram_http_client,
    ):
        telegram_bot = TelegramBot(
            http_client=telegram_http_client,
            token=TELEGRAM_BOT_TOKEN,
        )

        database = Database(database_connection)
        database.init()

        for locale in Locale:
            locale: Locale
            news_service = NewsService(
                locale=locale,
                http_client=news_http_client,
            )
            news_articles = news_service.get_news()

            article_ids = database.get_article_ids(locale)

            news_articles = [
                news_article for news_article in news_articles
                if news_article.id not in article_ids
            ]
            if news_articles:
                database.insert_article(news_articles)

                text = render_news_articles(news_articles)

                telegram_bot.send_message(
                    chat_id=CHAT_USERNAME,
                    text=text,
                )


if __name__ == '__main__':
    main()
