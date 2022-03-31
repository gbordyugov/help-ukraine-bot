"""put module docstring here"""
import logging
from functools import wraps
from typing import List, Optional, Tuple

from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Message,
    Update,
    Bot,
    BotCommand,
    ParseMode,
)
from telegram.error import BadRequest
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    InlineQueryHandler,
    Filters,
    CallbackContext,
    JobQueue,
    Job,
)
from telegram.utils.helpers import effective_message_type

import commands
from config import (
    APP_NAME,
    TOKEN,
    PORT,
    THUMB_URL,
    REMINDER_MESSAGE,
    REMINDER_INTERVAL_PINNED,
    REMINDER_INTERVAL_INFO,
    PINNED_JOB,
    SOCIAL_JOB,
    ADMIN_ONLY_CHAT_IDS,
    BERLIN_HELPS_UKRAIN_CHAT_ID,
    MONGO_HOST,
    MONGO_BASE,
    MONGO_PASS,
    MONGO_USER,
)
from guidebook import Guidebook, NameType
from models import Article
from mongo import connect
from services import Articles
from src.common import delete_command, reply_to_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

db = connect(MONGO_HOST, MONGO_USER, MONGO_PASS, MONGO_BASE)
TEST_CHAT = "tests"
articles_service = Articles(db, TEST_CHAT)

guidebook = Guidebook()


# Permissions
def restricted(func):
    """A decorator that limits the access to commands only for admins"""

    @wraps(func)
    def wrapped(bot: Bot, context: CallbackContext, *args, **kwargs):
        user_id = context.effective_user.id
        chat_id = context.effective_chat.id
        admins = [u.user.id for u in bot.get_chat_administrators(chat_id)]

        if user_id not in admins:
            logger.warning("Non admin attempts to access a restricted function")
            return

        logger.info("Restricted function permission granted")
        return func(bot, context, *args, **kwargs)

    return wrapped


def restricted_general(func):
    """A decorator that limits the access to commands only for admins"""

    @wraps(func)
    def wrapped(bot: Bot, context: CallbackContext, *args, **kwargs):
        user_id = context.effective_user.id
        chat_id = context.effective_chat.id
        chat = bot.get_chat(chat_id)
        if chat.type == "group":
            admins = [u.user.id for u in bot.get_chat_administrators(chat_id)]

            if chat_id in ADMIN_ONLY_CHAT_IDS:
                if user_id not in admins:
                    logger.warning("Non admin attempts to access a restricted function")
                    message_id = context.message.message_id
                    bot.delete_message(chat_id=chat_id, message_id=message_id)
                    return

        logger.info("Restricted function permission granted")
        return func(bot, context, *args, **kwargs)

    return wrapped


def send_social_reminder(bot: Bot, job: Job):
    """send_reminder"""
    chat_id = job.context
    logger.info("Sending a social reminder to chat %s", chat_id)
    results = guidebook.get_results(group_name=NameType.social_help, name=None)
    bot.send_message(chat_id=chat_id, text=results, disable_web_page_preview=True)


def send_pinned_reminder(bot: Bot, job: Job):
    """send_reminder"""
    chat_id = job.context
    chat = bot.get_chat(chat_id)
    msg: Message = chat.pinned_message
    logger.info("Sending pinned message to chat %s", chat_id)

    if msg:
        bot.forward_message(chat_id, chat_id, msg.message_id)
    else:
        bot.send_message(chat_id=chat_id, text=REMINDER_MESSAGE)


def delete_greetings(bot: Bot, update: Update) -> None:
    """Echo the user message."""
    message = update.message
    if message:
        msg_type = effective_message_type(message)
        logger.debug("Handling type is %s", msg_type)
        if effective_message_type(message) in [
            "new_chat_members",
            "left_chat_member",
        ]:
            bot.delete_message(chat_id=message.chat_id, message_id=message.message_id)


@restricted
def start_timer(bot: Bot, update: Update, job_queue: JobQueue):
    """start_timer"""
    message = update.message
    chat_id = message.chat_id
    command_message_id = message.message_id
    if chat_id in BERLIN_HELPS_UKRAIN_CHAT_ID:
        reminder(bot, update, job_queue)
    try:
        bot.delete_message(chat_id=chat_id, message_id=command_message_id)
    except BadRequest:
        logger.info("Command was already deleted %s", command_message_id)


@restricted
def admins_only(bot: Bot, update: Update):
    chat_id = update.message.chat_id
    ADMIN_ONLY_CHAT_IDS.append(chat_id)
    bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)


@restricted
def admins_only_revert(bot: Bot, update: Update):
    chat_id = update.message.chat_id
    ADMIN_ONLY_CHAT_IDS.remove(chat_id)
    bot.delete_message(chat_id=chat_id, message_id=update.message.message_id)


def reminder(bot: Bot, update: Update, job_queue: JobQueue):
    chat_id = update.message.chat_id
    logger.info("Started reminders in channel %s", chat_id)

    jobs: Tuple[Job] = job_queue.get_jobs_by_name(
        PINNED_JOB
    ) + job_queue.get_jobs_by_name(SOCIAL_JOB)

    #  Restart already existing jobs
    for job in jobs:
        if not job.enabled:
            job.enabled = True

    # Start a new job if there was none previously
    if not jobs:
        add_pinned_reminder_job(bot, update, job_queue)
        add_info_job(bot, update, job_queue)


def add_pinned_reminder_job(bot: Bot, update: Update, job_queue: JobQueue):
    chat_id = update.message.chat_id
    bot.send_message(
        chat_id=chat_id,
        text=f"I'm starting sending the pinned reminder every {REMINDER_INTERVAL_PINNED}s.",
    )
    job_queue.run_repeating(
        send_pinned_reminder,
        REMINDER_INTERVAL_PINNED,
        first=1,
        context=chat_id,
        name=PINNED_JOB,
    )


def add_info_job(bot: Bot, update: Update, job_queue: JobQueue):
    chat_id = update.message.chat_id
    bot.send_message(
        chat_id=chat_id,
        text=f"I'm starting sending the info reminder every {REMINDER_INTERVAL_INFO}s.",
    )
    job_queue.run_repeating(
        send_social_reminder,
        REMINDER_INTERVAL_INFO,
        first=1,
        context=chat_id,
        name=SOCIAL_JOB,
    )


@restricted
def stop_timer(bot: Bot, update: Update, job_queue: JobQueue):
    """stop_timer"""
    chat_id = update.message.chat_id

    #  Stop already existing jobs
    jobs: Tuple[Job] = job_queue.get_jobs_by_name(chat_id)
    for job in jobs:
        bot.send_message(chat_id=chat_id, text="I'm stopping sending the reminders.")
        job.enabled = False

    logger.info("Stopped reminders in channel %s", chat_id)


def find_articles_command(update: Update) -> None:
    """Handle the inline query."""
    query = update.inline_query.query

    articles = articles_service.find(query)
    results = [
        InlineQueryResultArticle(
            id=a.id,
            title=a.title,
            input_message_content=InputTextMessageContent(
                str(a), parse_mode=ParseMode.MARKDOWN
            ),
            thumb_url=THUMB_URL,
        )
        for a in articles
    ]

    update.inline_query.answer(results)


def get_param(bot, update, command):
    bot_name = bot.name
    return (
        update.message.text.removeprefix(command).replace(bot_name, "").strip().lower()
    )


def format_knowledge_results(results: str) -> str:
    separator = "=" * 30
    return separator + "\n" + results + "\n" + separator


def accomodation_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.accomodation, name=None)


def animal_help_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.animal_help, name=None)


def banking_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.banking, name=None)


def beauty_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.beauty, name=None)


def children_lessons_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.education, name="Онлайн уроки для детей")


def cities_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/cities")
    delete_command(bot, update)
    results = guidebook.get_cities(name=name)
    reply_to_message(bot, update, results)


def cities_all_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.cities, name=None)


def countries_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/countries")
    delete_command(bot, update)
    results = guidebook.get_countries(name=name)
    reply_to_message(bot, update, results)


def countries_all_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.countries, name=None)


def dentist_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.dentist, name=None)


def deutsch_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.german, name=None)


def disabled_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.disabled, name=None)


def education_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.education, name=None)


def entertainment_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.entertainment, name=None)


def evac_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.evacuation, name=None)


def evac_cities_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/evacuation_cities")
    guidebook.send_results(bot, update, group_name=NameType.evacuation_cities, name=name)


def freestuff_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/freestuff")
    guidebook.send_results(bot, update, group_name=NameType.freestuff, name=name)


def food_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.food, name=None)


def official_information_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.statements, name=None)


def general_information_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.general_information, name=None)


def germany_asyl_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/germany_asyl")
    delete_command(bot, update)
    results = guidebook.get_germany_asyl(name=name)
    reply_to_message(bot, update, results)


def germany_asyl_all_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.germany_asyl, name=None)


def handbook(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.handbook, name=None)


def help_command(bot: Bot, update: Update):
    delete_command(bot, update)
    results = format_knowledge_results(commands.help())
    reply_to_message(bot, update, results)


def homesharing_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.homesharing, name=None)


def hryvnia_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.hryvnia, name=None)


def humanitarian_aid_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.humanitarian, name=None)


def jobs_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.jobs, name=None)


def kids_with_special_needs_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.disabled, name="Помощь для детей с особыми потребностями")


def legal_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.legal, name=None)


def medical_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/medical")
    guidebook.send_results(bot, update, group_name=NameType.medical, name=name)


def meetup_command(bot: Bot, update: Update):
    name = get_param(bot, update, "/meetup")
    delete_command(bot, update)
    results = guidebook.get_meetup(name=name)
    reply_to_message(bot, update, results)


def minors_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.minors, name=None)


def psychological_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.psychological, name=None)


def photo_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.photo, name=None)


def social_adaption_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.social_adaptation, name=None)


def school_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.school, name=None)


def simcards_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.simcards, name=None)


def social_help_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.social_help, name=None)


def telegram_translation_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.telegram_translation, name=None)


def taxi_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.taxis, name=None)


def translators_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.translators, name=None)


def transport_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.transport, name=None)


def volunteer_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.volunteer, name=None)


def university_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.uni, name=None)


def vaccination_command(bot: Bot, update: Update):
    guidebook.send_results(bot, update, group_name=NameType.vaccination, name=None)


def parse_keys(line: str) -> List[str]:
    keys = line.split(" ")
    non_empty_keys = list(filter(lambda x: x.strip() != "", keys))
    return non_empty_keys


def parse_article(message: str, command: str, bot_name: str) -> Optional[Article]:
    message = message.text.removeprefix(command).replace(bot_name, "")
    lines = message.splitlines()
    if len(lines) < 3:
        return None
    else:
        keys = parse_keys(lines[0])
        if len(keys) < 1:
            return None
        else:
            title = lines[1]
            content = "".join(lines[2:])
            return Article(keys, title, content)


@restricted
def add_article_command(bot: Bot, update: Update):
    article = parse_article(update.message, "/add", bot.name)
    if article:
        articles_service.add(article)
        reply_to_message(bot, update, "article added")
    else:
        reply_to_message(bot, update, "Invalid message format")


@restricted
def list_articles_command(bot: Bot, update: Update):
    articles = articles_service.list()
    keys_title = "".join([str(a) for a in articles])
    msg = f"**Available articles:**\n{keys_title}"
    reply_to_message(bot, update, msg)


@restricted
def get_article_command(bot: Bot, update: Update):
    key = get_param(bot, update, "/faq")
    article = articles_service.get(key)
    keys = "".join(article.keys)
    message = f"**keys:** {keys}\n{article.title}\n{article.content}"
    reply_to_message(bot, update, message)


@restricted
def delete_article_command(bot: Bot, update: Update):
    key = get_param(bot, update, "/delete")
    articles_service.delete(key)
    message = f"key {key} deleted"
    reply_to_message(bot, update, message)


def get_command_list() -> List[BotCommand]:
    command_list = [
        BotCommand("accomodation", "Search accomodation"),
        BotCommand("adaption", "Social adaption in Berlin"),
        BotCommand("animals", "Animal help"),
        BotCommand("banking", "Banking information"),
        BotCommand("beauty", "Beauty"),
        BotCommand(
            "cities",
            "Find chats for German cities, you need to pass the name of the city",
        ),
        BotCommand(
            "cities_all",
            "List all chats for German cities",
        ),
        BotCommand("children_lessons", "Online lessons for children from Ukraine"),
        BotCommand("countries", "Find chats for counties, you need to pass the name of the city"),
        BotCommand("countries_all", "List all chats for countries"),
        BotCommand("dentist", "Dentist help"),
        BotCommand("deutsch", "German lessons"),
        BotCommand("disabled", "Disabled people"),
        BotCommand("education", "Overview of education in Germany"),
        BotCommand("entertainment", "Free entertainment"),
        BotCommand("evacuation", "General evacuation info"),
        BotCommand("evacuation_cities", "Evacuation chats for ukrainian cities"),
        BotCommand("food", "Where to get food in Berlin"),
        BotCommand("freestuff", "Free stuff in berlin"),
        BotCommand("general_information", "General information"),
        BotCommand("germany_asyl", "Germany-wide refugee centers, you need to pass the name of the Bundesland"),
        BotCommand("germany_asyl_all", "Germany-wide refugee centers"),
        BotCommand("handbook", "FAQ"),
        BotCommand("help", "Bot functionality"),
        BotCommand("hryvnia", "Hryvnia exchange"),
        BotCommand("humanitarian", "Humanitarian aid"),
        BotCommand("jobs", "Jobs in germany"),
        BotCommand("kids_with_special_needs", "Help for children with special needs"),
        BotCommand("legal", "Chat for legal help"),
        BotCommand("medical", "Medical help"),
        BotCommand("meetup", "meetups in Berlin"),
        BotCommand("minors", "Help for unaccompanied minors"),
        BotCommand("official_information", "Official information"),
        BotCommand("photo", "photo"),
        BotCommand("psychological", "Psychological help"),
        BotCommand("simcards", "simcards"),
        BotCommand("socialhelp", "Social help"),
        BotCommand("school", "Schools"),
        BotCommand("telegram_translation", "Telegram Translation"),
        BotCommand("taxis", "Taxi service"),
        BotCommand("translators", "Translators"),
        BotCommand("transport", "transport"),
        BotCommand("uni", "Universities in Germany"),
        BotCommand("vaccination", "vaccination information"),
        BotCommand("volunteer", "Volunteer"),
    ]
    command_list.sort(key=lambda x: x.command)
    return command_list


def add_commands(dispatcher):
    # Commands
    dispatcher.add_handler(CommandHandler("start", start_timer, pass_job_queue=True))
    dispatcher.add_handler(CommandHandler("stop", stop_timer, pass_job_queue=True))
    dispatcher.add_handler(CommandHandler("help", help_command))

    dispatcher.add_handler(CommandHandler("adminsonly", admins_only))
    dispatcher.add_handler(CommandHandler("adminsonly_revert", admins_only_revert))
    dispatcher.add_handler(CommandHandler("accomodation", accomodation_command))
    dispatcher.add_handler(CommandHandler("animals", animal_help_command))
    dispatcher.add_handler(CommandHandler("adaption", social_adaption_command))
    dispatcher.add_handler(CommandHandler("banking", banking_command))
    dispatcher.add_handler(CommandHandler("beauty", beauty_command))
    dispatcher.add_handler(CommandHandler("children_lessons", children_lessons_command))
    dispatcher.add_handler(CommandHandler("cities", cities_command))
    dispatcher.add_handler(CommandHandler("cities_all", cities_all_command))
    dispatcher.add_handler(CommandHandler("countries", countries_command))
    dispatcher.add_handler(CommandHandler("countries_all", countries_all_command))
    dispatcher.add_handler(CommandHandler("dentist", dentist_command))
    dispatcher.add_handler(CommandHandler("deutsch", deutsch_command))
    dispatcher.add_handler(CommandHandler("disabled", disabled_command))
    dispatcher.add_handler(CommandHandler("education", education_command))
    dispatcher.add_handler(CommandHandler("entertainment", entertainment_command))
    dispatcher.add_handler(CommandHandler("evacuation", evac_command))
    dispatcher.add_handler(CommandHandler("evacuation_cities", evac_cities_command))
    dispatcher.add_handler(CommandHandler("freestuff", freestuff_command))
    dispatcher.add_handler(CommandHandler("food", food_command))
    dispatcher.add_handler(
        CommandHandler("general_information", general_information_command)
    )
    dispatcher.add_handler(CommandHandler("germany_asyl", germany_asyl_command))
    dispatcher.add_handler(CommandHandler("germany_asyl_all", germany_asyl_all_command))
    dispatcher.add_handler(CommandHandler("handbook", handbook))
    dispatcher.add_handler(CommandHandler("homesharing", homesharing_command))
    dispatcher.add_handler(CommandHandler("hryvnia", hryvnia_command))
    dispatcher.add_handler(CommandHandler("humanitarian", humanitarian_aid_command))
    dispatcher.add_handler(CommandHandler("jobs", jobs_command))
    dispatcher.add_handler(
        CommandHandler("kids_with_special_needs", kids_with_special_needs_command)
    )
    dispatcher.add_handler(CommandHandler("legal", legal_command))
    dispatcher.add_handler(CommandHandler("medical", medical_command))
    dispatcher.add_handler(CommandHandler("meetup", meetup_command))
    dispatcher.add_handler(CommandHandler("minors", minors_command))
    dispatcher.add_handler(
        CommandHandler("official_information", official_information_command)
    )
    dispatcher.add_handler(CommandHandler("photo", photo_command))
    dispatcher.add_handler(CommandHandler("psychological", psychological_command))
    dispatcher.add_handler(CommandHandler("school", school_command))
    dispatcher.add_handler(CommandHandler("simcards", simcards_command))
    dispatcher.add_handler(CommandHandler("socialhelp", social_help_command))
    dispatcher.add_handler(CommandHandler("taxis", taxi_command))
    dispatcher.add_handler(CommandHandler("translators", translators_command))
    dispatcher.add_handler(CommandHandler("telegram_translation", telegram_translation_command))
    dispatcher.add_handler(CommandHandler("transport", transport_command))
    dispatcher.add_handler(CommandHandler("uni", university_command))
    dispatcher.add_handler(CommandHandler("vaccination", vaccination_command))
    dispatcher.add_handler(CommandHandler("volunteer", volunteer_command))

    # Articles
    dispatcher.add_handler(CommandHandler("add", add_article_command))
    dispatcher.add_handler(CommandHandler("list", list_articles_command))
    dispatcher.add_handler(CommandHandler("faq", get_article_command))
    dispatcher.add_handler(CommandHandler("delete", delete_article_command))


def main() -> None:
    """Start the bot."""
    # Create the Updater and pass it your bot's token.
    updater = Updater(TOKEN)

    # Get the dispatcher to register handlers
    dispatcher = updater.dispatcher

    add_commands(dispatcher)
    command_list = get_command_list()
    updater.bot.set_my_commands(command_list)

    # Messages
    dispatcher.add_handler(MessageHandler(Filters.all, delete_greetings))

    # Inlines
    dispatcher.add_handler(InlineQueryHandler(find_articles_command))

    if APP_NAME == "TESTING":
        updater.start_polling()
    else:
        updater.start_webhook(listen="0.0.0.0", port=int(PORT), url_path=TOKEN)
        updater.bot.setWebhook(f"https://{APP_NAME}.herokuapp.com/{TOKEN}")

    updater.idle()


if __name__ == "__main__":
    main()
