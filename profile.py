"""
Single source of truth for who the writer is and what they have actually built.

Ground truth read directly from krish-portfolio-dev.vercel.app (homepage SEO
markup + sitemap.xml) on 2026-10-16. Both topic_picker.py and generate_post.py
import this so they can never disagree about the facts.

If you build something new or correct a description, edit it here only.
"""

from __future__ import annotations

from urllib.parse import urlparse

# ---------------------------------------------------------------- identity
IDENTITY = """Krish Khati. Motivated Class 12 student from Pundri, Kaithal,
Haryana, India. No formal tech background, no coding classes - everything was
self-taught with a laptop. Currently looking for an entry-level role or
internship where he can apply his problem-solving mindset to real software.

Real skills and tools he actually uses:
  Python, C, C++ | web development | AI tools | Claude Code | Supabase
  Git & GitHub | video editing | content creation

Self-description in his own words: "ALWAYS BUILDING", "LOGIC FIRST"."""

# ---------------------------------------------------------------- projects
PROJECTS: dict[str, dict] = {
    "Fitness Temple": {
        "is": "a modern, high-energy gym website (black-and-red themed)",
        "facts": [
            "hero section",
            "programs / disciplines showcase",
            "stats bar with ratings, members and years active",
            "smooth navigation",
        ],
        "page": "/projects/fitness-temple",
    },
    "LinguaBridge": {
        "is": "an AI-powered language TRANSLATION tool that bridges communication "
              "across languages using AI",
        "facts": [
            "AI-powered translation",
            "bridging communication across languages",
        ],
        "page": "/projects/linguabridge",
    },
    "Memory Match Game": {
        "is": "an interactive memory-matching arcade game with a gradient UI, "
              "real-time audio components and a 'Launch Arcade' entry screen",
        "facts": [
            "memory-matching arcade gameplay",
            "gradient UI",
            "real-time audio components",
            "'Launch Arcade' entry screen",
            "later published as a free Android app on the Google Play Store",
        ],
        "page": "/projects/memory-match-game",
    },
    "Calculator": {
        "is": "a sleek modern calculator app with a dark-themed UI, circular "
              "button design and smooth interactions",
        "facts": [
            "basic arithmetic operations",
            "dark-themed UI",
            "circular button design",
        ],
        "page": "/projects/calculator",
    },
}

# Side projects, referenced in his own blog index.
OTHER_WORK = [
    "a Python chatbot with Supabase authentication and vector search "
    "(Gemini embeddings)",
]

PORTFOLIO_PAGES = {
    "home": "/",
    "blog": "/blog",
    "projects index": "/projects",
    "faq": "/faq",
}

GITHUB_PROFILE = "https://github.com/krish-khati"

# ------------------------------------------------------------------- links
# The ONLY URLs a post is allowed to link to. Verified live against the
# sitemap on 2026-10-16. The model picks a key from this dict; it can never
# invent a URL.
LINK_TARGETS: dict[str, str] = {
    "home": "https://krish-portfolio-dev.vercel.app/",
    "projects": "https://krish-portfolio-dev.vercel.app/projects",
    "blog": "https://krish-portfolio-dev.vercel.app/blog",
    "faq": "https://krish-portfolio-dev.vercel.app/faq",
    "project_fitness_temple": "https://krish-portfolio-dev.vercel.app/projects/fitness-temple",
    "project_linguabridge": "https://krish-portfolio-dev.vercel.app/projects/linguabridge",
    "project_memory_match": "https://krish-portfolio-dev.vercel.app/projects/memory-match-game",
    "project_calculator": "https://krish-portfolio-dev.vercel.app/projects/calculator",
    "github": "https://github.com/krish-khati",
}

# Site-wide pages, as opposed to individual project pages. A "general" post
# has no obvious tie to one specific project, so its close link belongs on one
# of these rather than on a random project page or on GitHub.
SITE_TARGETS: set[str] = {"home", "projects", "blog", "faq"}

# The portfolio's own domain, derived from the verified table rather than
# hardcoded, so it can never drift from LINK_TARGETS. Every post must link
# here at least once - linking only to GitHub defeats the point of the system.
PORTFOLIO_DOMAIN: str = urlparse(LINK_TARGETS["home"]).netloc


def is_portfolio(target: str) -> bool:
    """True if this link target resolves to a page on the portfolio site."""
    url = LINK_TARGETS.get(target, "")
    return bool(url) and urlparse(url).netloc == PORTFOLIO_DOMAIN


# Concrete anchor examples per target. The validator bans every generic
# phrase, and with only nine pages the model kept guessing the same handful.
# Showing it what "specific" means per page is far more effective than a
# blocklist alone.
ANCHOR_EXAMPLES: dict[str, list[str]] = {
    "home": ["what I have been building", "where I keep my notes on building",
             "the site I maintain"],
    "projects": ["the four apps I have finished",
                 "the projects listed on my site",
                 "what I have actually shipped"],
    "blog": ["where I write up what I learn",
             "my longer notes on building software",
             "the write-ups I keep here"],
    "faq": ["answers to questions I keep being asked",
            "short answers I wrote down"],
    "project_fitness_temple": ["the gym landing page I built",
                               "the black and red site I made",
                               "how I laid out the programs section"],
    "project_linguabridge": ["the translation tool I built",
                             "the app that bridges two languages",
                             "how I wired up the translation call"],
    "project_memory_match": ["the card matching game I built",
                             "the arcade game with the launch screen",
                             "how I handled the flipped cards"],
    "project_calculator": ["the calculator I built",
                           "the dark themed calculator app",
                           "how I handled the button states"],
    "github": ["the repos I have pushed",
               "what I have committed so far",
               "where my code actually lives"],
}


# Rough guidance so the two links in a post do not collapse onto one page.
BODY_TARGET_HINTS = [
    "project_fitness_temple", "project_linguabridge", "project_memory_match",
    "project_calculator", "projects", "github",
]
CLOSE_TARGET_HINTS = ["home", "projects", "blog", "github"]

# ------------------------------------------------- topics ALREADY published
# Scraped from the portfolio sitemap on 2026-10-16. Never repeat these angles,
# even though posted_topics.json has no record of them yet - the ledger only
# knows about posts this tool has made.
ALREADY_PUBLISHED_TOPICS = [
    "Adding vector search to a Python chatbot with Supabase",
    "Adding Supabase auth to a Python chatbot, step by step",
    "Publishing a first game on the Google Play Store for free",
    "Why a portfolio website instead of relying on a resume",
    "Automating blog posts for free with AI tools",
    "Building a free government scheme finder",
    "Building a Python voice assistant",
    "Learning to code with just a laptop",
    "What Python, C and C++ each taught me",
    "What a memory match game taught me about state management",
    "How I use AI tools without outsourcing my thinking",
    "What digital marketing with AI taught me",
]

INTERESTS = [
    # --- personal journey (his actual experience) ---
    "using AI tools in a real self-taught developer workflow",
    "content creation and video editing as a self-taught skill",
    "building and shipping small GitHub projects",
    "learning C, C++ and Python without a classroom",
    "shipping a Class 12 student's side projects to real users",
    "how a portfolio site helps when you are job hunting",

    # --- general tech / SEO / marketing content ---
    # Wider scope: evergreen explainers a reader outside India would search for.
    # These are still written in his voice, but do NOT require a personal
    # anecdote, so the model must not be pushed toward inventing one.
    "practical SEO explainers: meta descriptions, internal linking, crawl basics",
    "technical SEO: Core Web Vitals, page speed, structured data, sitemaps",
    "search intent, keyword clusters and how to research them properly",
    "SEM basics: Google Ads structure, ad copy, and measuring real ROI",
    "AI writing tools: what they are genuinely good and bad at",
    "prompting techniques that actually improve output quality",
    "how AI coding assistants work, and where they still fail",
    "version control fundamentals for solo developers",
    "reading documentation well, and learning to debug from error messages",
    "choosing a first programming language and what it costs you",
    "building a personal brand as a junior developer",
    "free tools that genuinely replace paid ones for a beginner",
    "clean code basics that survive contact with a real project",
    "what a recruiter actually looks at in a junior developer's portfolio",
    "content marketing basics: topics, cadence, and measuring what works",
    "video editing fundamentals for talking-head and screen content",
    "email marketing fundamentals for a small creator audience",
    "landing page structure and the handful of elements that matter",
]

# Topics that need no personal story. The generator is told these explicitly
# so a general explainer does not get padded with invented experience.
GENERAL_TOPIC_MARKERS = (
    "seo", "sem", "google ads", "keyword", "meta description", "sitemap",
    "core web vitals", "page speed", "structured data", "search intent",
    "internal linking", "crawl", "schema markup", "canonical",
    "explainer", "how to", "what is", "fundamentals", "basics", "guide",
)

HARD_RULES = [
    "NEVER invent a personal anecdote, client, job, team, or revenue figure.",
    "NEVER state a metric (traffic, earnings, followers, signups) that was not given.",
    "NEVER claim professional experience, a degree, or a job title.",
    "LinguaBridge is a TRANSLATION tool, not a language-learning app and it has "
    "no blog page and no SEO layer.",
    "The /blog page belongs to the portfolio site itself, not to any project.",
    "If a needed detail is unknown, write around it in general terms.",
]

BANNED_OPENERS = [
    "In today's fast-paced world",
    "In the ever-evolving digital landscape",
    "In an era where",
    "Let's dive in",
    "Let's dive into",
    "Buckle up",
    "Are you ready to",
    "Picture this",
    "Let that sink in",
]

AI_TELLS = [
    "repeating the same idea in different words",
    "tricolour or alliterative lists that exist only to sound clever",
    "phrases like 'it's not just X, it's Y'",
    "a closing paragraph that merely restates the article",
    "fake vulnerability or 'let me tell you a story' framing",
    "em-dash-heavy staccato rhythm",
    "any sentence that would read equally well in any other article",
]


# ------------------------------------------------------------------ helpers
def _fmt_projects() -> str:
    lines = []
    for name, p in PROJECTS.items():
        lines.append(f"  {name} - {p['is']}")
        lines.append(f"      real details: {'; '.join(p['facts'])}")
        lines.append(f"      page: {p['page']}")
    return "\n".join(lines)


def ground_truth() -> str:
    """The block injected into every prompt."""
    return f"""
WHO IS WRITING:
{IDENTITY}

REAL PROJECTS (these four are the only portfolio projects that exist):
{_fmt_projects()}

ALSO BUILT:
{chr(10).join(f'  - {o}' for o in OTHER_WORK)}

REAL ASSETS:
  portfolio home : https://krish-portfolio-dev.vercel.app/
  portfolio blog : https://krish-portfolio-dev.vercel.app/blog
  projects index : https://krish-portfolio-dev.vercel.app/projects
  faq            : https://krish-portfolio-dev.vercel.app/faq
  github         : {GITHUB_PROFILE}

HARD RULES:
{chr(10).join(f'  - {r}' for r in HARD_RULES)}
""".strip()


def published_block() -> str:
    return (
        "ALREADY PUBLISHED on the portfolio blog - these angles are taken, do not "
        "repeat or lightly rehash any of them:\n"
        + "\n".join(f"  - {t}" for t in ALREADY_PUBLISHED_TOPICS)
    )


def scope_block() -> str:
    """Instruction that lets topics go general without forcing an anecdote."""
    return """
SCOPE - READ THIS CAREFULLY:
You may write about GENERAL technology, SEO, SEM and content-marketing topics
that have nothing to do with Krish personally. Technical explainers, SEO guides
and marketing fundamentals are explicitly in scope.

For a GENERAL topic:
- Write it as an explainer, in his voice but about the subject itself.
- Do NOT invent a personal anecdote to justify it. No "when I did X at my
  job", no invented client, no invented metric.
- It is fine for a general post to contain NO first-person experience at all.
- You may use "you" freely in a general explainer.

For a PERSONAL topic, the rules above apply in full: only what he actually did.

Pick whichever fits the topic. Do not force every post into a personal story.
""".strip()


def bans_block() -> str:
    return (
        "BANNED OPENERS (never start an article with these):\n"
        + "\n".join(f'  - "{o}"' for o in BANNED_OPENERS)
        + "\n\nWRITING TELLS to avoid:\n"
        + "\n".join(f"  - {t}" for t in AI_TELLS)
    )