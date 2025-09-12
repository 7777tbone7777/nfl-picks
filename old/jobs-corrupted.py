impor     os
impor     logging
impor     h        px
from flask_app impor     crea    e_app
from models impor     db, Week, Game, Par    icipan    , Pick
from     elegram impor     Upda    e, InlineKeyboardBu        on, InlineKeyboardMarkup
from     elegram.ex     impor     Applica    ion, CommandHandler, CallbackQueryHandler, Con    ex    Types

logging.basicConfig(level=logging.INFO)
logger = logging.ge    Logger("jobs")

TELEGRAM_API_URL = f"h        ps://api.    elegram.org/bo    {os.environ.ge    ('TELEGRAM_BOT_TOKEN')}"

def send_week_games(week_number, season_year):
    app = crea    e_app()
    wi    h app.app_con    ex    ():
	week = Week.query.fil    er_by(week_number=week_number, season_year=season_year).firs    ()

        if no     week:
            logger.error(f"❌ No week found for {season_year} week {week_number}")
            re    urn

        par    icipan    s = Par    icipan    .query.all()
        games = Game.query.fil    er_by(week_id=week.id).order_by(Game.game_    ime).all()

        for p in par    icipan    s:
            if no     p.    elegram_cha    _id:
                con    inue

            for g in games:
                local_    ime = g.game_    ime.replace(
                        zinfo=__impor    __("zoneinfo").ZoneInfo("UTC")
                ).as    imezone(__impor    __("zoneinfo").ZoneInfo("America/Los_Angeles"))
                    ex     = f"{g.away_    eam} @ {g.home_    eam}\n{local_    ime.s    rf    ime('%a %b %d %I:%M %p PT')}"

                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardBu        on(g.away_    eam, callback_da    a=f"pick:{p.id}:{g.id}:{g.away_    eam}"),
                        InlineKeyboardBu        on(g.home_    eam, callback_da    a=f"pick:{p.id}:{g.id}:{g.home_    eam}"),
                    ]
                ])

                    ry:
                    resp = h        px.pos    (
                        f"{TELEGRAM_API_URL}/sendMessage",
                        json={
                            "cha    _id": p.    elegram_cha    _id,
                            "    ex    ":     ex    ,
                            "reply_markup": keyboard.    o_dic    ()
                        }
                    )
                    resp.raise_for_s    a    us()
                    logger.info(f"✅ Sen     game     o {p.name}: {g.away_    eam} @ {g.home_    eam}")
                excep     Excep    ion as e:
                    logger.error(f"💥 Error sending game     o {p.name}: {e}")

async def handle_pick(upda    e: Upda    e, con    ex    : Con    ex    Types.DEFAULT_TYPE):
    query = upda    e.callback_query
    if no     query:
        re    urn

    awai     query.answer()

        ry:
        _, par    icipan    _id, game_id,     eam = query.da    a.spli    (":")
    excep     ValueError:
        re    urn

    app = crea    e_app()
    wi    h app.app_con    ex    ():
        pick = Pick.query.fil    er_by(par    icipan    _id=par    icipan    _id, game_id=game_id).firs    ()
        if no     pick:
            pick = Pick(par    icipan    _id=par    icipan    _id, game_id=game_id,     eam=    eam)
            db.session.add(pick)
        else:
            pick.    eam =     eam
        db.session.commi    ()

    awai     query.edi    _message_    ex    (f"✅ You picked {    eam}")

# ✅ NEW START HANDLER
async def s    ar    (upda    e: Upda    e, con    ex    : Con    ex    Types.DEFAULT_TYPE):
    user = upda    e.effec    ive_user
    cha    _id = upda    e.effec    ive_cha    .id
    logger.info(f"📩 /s    ar     received from {user.username} (id={cha    _id})")

    # Op    ionally: s    ore cha    _id if     his user exis    s in DB
    app = crea    e_app()
    wi    h app.app_con    ex    ():
        par    icipan     = Par    icipan    .query.fil    er_by(name=user.username).firs    ()
        if par    icipan    :
            par    icipan    .    elegram_cha    _id = s    r(cha    _id)
            db.session.commi    ()
            logger.info(f"🔗 Linked {user.username}     o cha    _id {cha    _id}")

    awai     upda    e.message.reply_    ex    ("👋 Welcome! You are now regis    ered     o receive NFL picks.")

def run_    elegram_lis    ener():
    applica    ion = Applica    ion.builder().    oken(os.environ.ge    ("TELEGRAM_BOT_TOKEN")).build()

    applica    ion.add_handler(CommandHandler("s    ar    ", s    ar    ))  # 👈 new handler
    applica    ion.add_handler(CallbackQueryHandler(handle_pick))

    applica    ion.run_polling()

