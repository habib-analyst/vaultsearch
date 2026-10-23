#!/usr/bin/env python3
"""Generate the synthetic multi-tenant corpus + labeled queries for vaultsearch.

Design: 4 tenants x 4 topics x 75 docs = 1200 documents.
Each tenant is a distinct domain so cross-tenant bleed is obvious.
Topics pair into 2 clusters per tenant: graded relevance for a query is
  2 = same topic, 1 = sibling topic in the same cluster, 0 = anything else.
Queries are generated from topic metadata in two styles (keyword / paraphrase)
so the lexical and vector arms have different strengths by construction.
Output: corpus.jsonl (docs) and queries.jsonl (queries + topic/cluster labels).
Deterministic: seeded RNG.
"""
import json
import random
import os

random.seed(20261023)

HERE = os.path.dirname(os.path.abspath(__file__))

# Each topic: name, cluster, core terms (lexical signature), entities,
# sentence templates ({e}=entity, {t}=term, {n}=number, {u}=unit-ish word).
TENANTS = {
    "acme-cooking": {
        "domain": "culinary studio knowledge base",
        "topics": [
            dict(name="sourdough fermentation", cluster="breads",
                 terms=["sourdough", "starter", "levain", "fermentation", "bulk ferment", "gluten", "crumb", "proofing"],
                 entities=["wheat levain", "rye starter", "stone-milled flour", "banneton", "dutch oven"],
                 templates=[
                     "Maintain the {e} with daily feedings of flour and water so the {t} stays active and predictable.",
                     "During {t}, watch for doming and a 40 percent rise before shaping; under-proofed loaves bake dense.",
                     "A healthy {t} culture smells fruity and slightly tangy, never like nail polish remover.",
                     "For a more open {t}, raise hydration gradually and extend the {t} at cool room temperature.",
                     "Record starter age, feed ratio and ambient temperature for every batch of {t} bread."]),
            dict(name="baking science", cluster="breads",
                 terms=["maillard", "oven spring", "steam injection", "crust", "enzymes", "gelatinization", "scoring", "dough temperature"],
                 entities=["convection oven", "baking steel", "lame blade", "spray bottle", "dough sheeter"],
                 templates=[
                     "The {t} reaction gives the {t} its deep colour and roasted flavour above 140 degrees Celsius.",
                     "Use the {e} during the first ten minutes to maximise {t} before the crust sets.",
                     "Hold dough at a final {t} of 24 to 26 degrees; warmer dough ferments faster than the schedule assumes.",
                     "Sharp {t} with the {e} at a shallow angle lets the loaf expand along the cut instead of bursting.",
                     "Starch {t} begins near 60 degrees and is why crumb structure sets as the loaf cools."]),
            dict(name="spice blending", cluster="seasoning",
                 terms=["spice blend", "toasting", "cumin", "coriander", "smoked paprika", "turmeric", "grinding", "blooming"],
                 entities=["cast iron skillet", "spice grinder", "mortar and pestle", "glass jar", "mustard seeds"],
                 templates=[
                     "{t} whole spices in a dry {e} until fragrant, about ninety seconds, then cool before {t}.",
                     "A balanced {t} layers heat, earthiness and acid; taste and adjust salt only at the end.",
                     "Store every {t} in a sealed {e} away from light; ground spices lose potency within six months.",
                     "{t} spices in hot oil for thirty seconds releases fat-soluble aromatics into the dish.",
                     "Grind the {t} in small batches with the {e} so volatile oils are not lost to heat."]),
            dict(name="pickling and preserves", cluster="seasoning",
                 terms=["brine", "pickling", "vinegar", "fermented vegetables", "sterilising jars", "pH", "canning", "quick pickle"],
                 entities=["mason jar", "cucumber spears", "pickling crock", "funnel", "red onion"],
                 templates=[
                     "A basic {t} is equal parts {t} and water with two percent salt by weight for vegetables.",
                     "Keep vegetables submerged below the {t} surface; anything exposed to air invites mould.",
                     "Check that the finished {t} sits below pH 4.0 before storing jars at room temperature.",
                     "For a {t}, slice the {e} thin, salt for an hour, then cover with hot spiced vinegar.",
                     "Wash and {t} every {e} in boiling water for ten minutes before filling."]),
        ],
    },
    "benco-auto": {
        "domain": "vehicle repair workshop manual archive",
        "topics": [
            dict(name="brake systems", cluster="chassis",
                 terms=["brake pads", "rotor", "caliper", "brake fluid", "bleeding brakes", "ABS", "pedal travel", "torque wrench"],
                 entities=["front caliper", "brake line", "master cylinder", "wheel hub", "bleeder valve"],
                 templates=[
                     "Measure {t} thickness at three points; replace the {e} if runout exceeds 0.05 mm.",
                     "After fitting new {t}, {t} in the correct sequence to purge air from the lines.",
                     "A spongy pedal with long {t} usually means air or moisture in the {t}.",
                     "Always torque the {e} bolts to specification with a calibrated {t} in a star pattern.",
                     "If the {t} warning light stays on after bleeding, inspect the {e} switch and fluid level sensor."]),
            dict(name="suspension and steering", cluster="chassis",
                 terms=["strut", "control arm", "tie rod", "wheel alignment", "camber", "ball joint", "bushing", "steering rack"],
                 entities=["coil spring", "shock absorber", "sway bar link", "alignment rack", "pitman arm"],
                 templates=[
                     "Worn {t} rubber shows as clunks over bumps and vague steering; press in a new {e} rather than reusing the arm.",
                     "After replacing the {e}, a full {t} is mandatory before the vehicle returns to service.",
                     "Check {t} play with the wheel raised; more than a millimetre of movement condemns the joint.",
                     "Uneven tyre wear with excessive negative {t} points to a bent {e} or sagging spring.",
                     "Compress the {e} only with a rated spring compressor; never release a loaded strut by hand."]),
            dict(name="engine diagnostics", cluster="powertrain",
                 terms=["OBD-II", "fault code", "misfire", "compression test", "spark plug", "fuel trim", "oxygen sensor", "timing chain"],
                 entities=["scan tool", "intake manifold", "coil pack", "throttle body", "crankshaft sensor"],
                 templates=[
                     "Read the stored {t} with the {e} before clearing anything; freeze-frame data shows the conditions at the fault.",
                     "A single-cylinder {t} that follows the {e} when swapped confirms the coil, not the plug, is at fault.",
                     "Long-term {t} above ten percent suggests a vacuum leak downstream of the {e}.",
                     "Run a {t} on all cylinders; readings within ten percent of each other rule out ring wear.",
                     "A stretched {t} sets correlation codes between the {e} and camshaft position sensors."]),
            dict(name="transmission service", cluster="powertrain",
                 terms=["gearbox", "clutch", "torque converter", "ATF", "shift solenoid", "synchromesh", "differential", "fluid flush"],
                 entities=["valve body", "flywheel", "release bearing", "gear selector", "oil pan"],
                 templates=[
                     "Drain the {t} warm, inspect the {e} magnet for swarf, and refill to the marked level only.",
                     "A slipping {t} under load with burnt-smelling fluid usually means the friction plates are glazed.",
                     "Harsh engagement points to a sticking {t} in the {e}; log shift times before condemning the box.",
                     "Bleed the hydraulic {t} at the slave cylinder after any work on the {e}.",
                     "Use only the specified {t}; the wrong specification causes shudder in the {t} within a few hundred miles."]),
        ],
    },
    "verdant-garden": {
        "domain": "horticulture cooperative advisory notes",
        "topics": [
            dict(name="soil health", cluster="growing",
                 terms=["soil pH", "compost", "organic matter", "nitrogen", "mulch", "soil test", "loam", "microbes"],
                 entities=["raised bed", "cover crop", "worm bin", "spade", "rain gauge"],
                 templates=[
                     "Take a {t} every autumn; most vegetables want a {t} between 6.0 and 7.0.",
                     "Spread five centimetres of {t} over the {e} each spring to feed soil {t}.",
                     "Legume {t} fixes {t} that the following brassica crop will use.",
                     "Heavy clay improves fastest with {t} and patience, not with sand.",
                     "Keep the {e} covered with {t} through winter to stop nutrient leaching."]),
            dict(name="irrigation and water", cluster="growing",
                 terms=["drip irrigation", "watering schedule", "drought", "soaker hose", "moisture meter", "rainwater harvesting", "capillary", "wilting"],
                 entities=["water butt", "drip line", "timer valve", "mulched border", "clay pot"],
                 templates=[
                     "Run the {t} at dawn so less water is lost to evaporation and leaves dry before night.",
                     "Check soil moisture with a {t} at ten centimetres before following any fixed {t}.",
                     "Group plants by thirst along one {e} run so thirsty crops are not drowned or starved.",
                     "A 200 litre {e} fills from an average roof in a single wet week of {t} storage.",
                     "Deep, infrequent {t} grows deeper roots than daily sprinkling and resists {t} better."]),
            dict(name="pest management", cluster="protection",
                 terms=["aphids", "slug control", "companion planting", "neem", "flea beetle", "biological control", "crop rotation", "netting"],
                 entities=["ladybird larvae", "beer trap", "mesh tunnel", "marigold border", "inspection loupe"],
                 templates=[
                     "Blast {t} colonies off with water before reaching for {t}; most wash off and do not return.",
                     "A {e} at soil level catches more slugs per night than pellets and spares the hedgehogs.",
                     "{t} breaks pest cycles; never follow potatoes with tomatoes on the same bed.",
                     "Cover brassicas with the {e} from planting to keep flea beetle and cabbage white out.",
                     "Encourage {e} by leaving one untidy corner; they eat more {t} than any spray."]),
            dict(name="pruning and training", cluster="protection",
                 terms=["pruning", "deadheading", "espalier", "secateurs", "cordon", "thinning fruit", "winter cut", "bud union"],
                 entities=["loppers", "pruning saw", "fan-trained peach", "step ladder", "healing compound"],
                 templates=[
                     "Make every {t} just above an outward-facing bud with clean, sharp {e}.",
                     "An {t} against a warm wall ripens fruit two weeks earlier than a free-standing tree.",
                     "{t} in late winter while the tree is dormant; summer cuts check growth instead.",
                     "Remove no more than a fifth of the canopy in one {t} season to avoid watershoots.",
                     "Thin the {t} to one fruit per cluster after the June drop for larger apples."]),
        ],
    },
    "ledgerly-finance": {
        "domain": "personal finance guidance library",
        "topics": [
            dict(name="budgeting basics", cluster="planning",
                 terms=["budget", "emergency fund", "sinking fund", "cash flow", "50/30/20 rule", "direct debit", "spending audit", "pay yourself first"],
                 entities=["current account", "budgeting app", "standing order", "envelope system", "household ledger"],
                 templates=[
                     "Build a starter {t} of one month of essentials before investing a single pound.",
                     "Route savings by {e} on payday; the {t} principle fails when it depends on willpower.",
                     "A monthly {t} of the last ninety days of statements finds the leaks a {t} hides.",
                     "Give irregular bills a {t} so December does not ambush your {t}.",
                     "Under the {t}, cap needs at half of take-home pay and review the split quarterly."]),
            dict(name="retirement planning", cluster="planning",
                 terms=["pension", "compound growth", "retirement age", "annuity", "drawdown", "employer match", "tax relief", "index fund"],
                 entities=["workplace pension", "SIPP", "state pension", "retirement calculator", "beneficiary form"],
                 templates=[
                     "Never leave the {t} on the table; it is an instant doubling of that slice of pay.",
                     "Starting a {e} ten years earlier roughly doubles the pot at the same monthly contribution, thanks to {t}.",
                     "In {t}, sequence risk in the first five years matters more than average returns.",
                     "Claim the {t} you are owed on {e} contributions; higher-rate relief is not automatic.",
                     "Review the {e} annually and rebalance back to your target {t} allocation."]),
            dict(name="credit and debt", cluster="risk",
                 terms=["credit score", "APR", "minimum payment", "debt avalanche", "balance transfer", "credit utilisation", "default", "arrears"],
                 entities=["credit card", "overdraft", "credit report", "debt charity", "payment holiday"],
                 templates=[
                     "Keep {t} below thirty percent of each limit to protect your {t}.",
                     "The {t} method clears the highest {t} first and minimises total interest paid.",
                     "A {t} at zero percent only helps if the balance is gone before the promotional window closes.",
                     "Paying only the {t} on a card can stretch a small balance across a decade of interest.",
                     "Check your {e} from all three agencies before any mortgage application; errors cause {t} markers."]),
            dict(name="insurance essentials", cluster="risk",
                 terms=["insurance premium", "excess", "underwriting", "term life cover", "income protection", "policy exclusion", "renewal quote", "no-claims bonus"],
                 entities=["life insurance policy", "home contents policy", "insurer helpline", "broker", "policy document"],
                 templates=[
                     "Raising the voluntary {t} cuts the {t} but only insure risks you can actually fund.",
                     "Most {t} claims fail on a {t} buried in the small print, not on the headline cover.",
                     "{t} matters more than life cover for most working households; it pays when you cannot work.",
                     "Never auto-accept a {t}; the same insurer often quotes new customers less for identical cover.",
                     "Disclose everything at {t}; an undisclosed condition voids the {e} when it matters most."]),
        ],
    },
}

GENERIC_SENTENCES = [
    "Keep notes from every session so the next review starts from evidence, not memory.",
    "Small consistent adjustments beat occasional drastic changes in almost every case.",
    "When results stall, change one variable at a time and measure again after a week.",
    "Beginners usually underestimate how much the basics matter compared to advanced tricks.",
]

# paraphrase pools per query style, keyed by nothing topic-specific;
# paraphrase queries avoid the topic's core terms where possible.
QUERY_TEMPLATES_KEYWORD = [
    "{t1} and {t2} guide",
    "how to handle {t1}",
    "{t1} {t2} problems",
    "best practice {t1}",
    "{t1} explained",
    "{t2} checklist {t1}",
]
QUERY_TEMPLATES_PARAPHRASE = [
    "what should I do when things go wrong with my {topic}",
    "advice for getting better results with {topic}",
    "common mistakes people make with {topic}",
    "step by step help with {topic}",
    "why is my {topic} not working as expected",
    "tips from experienced practitioners on {topic}",
]


def make_docs():
    docs = []
    for tenant, spec in TENANTS.items():
        n = 0
        for topic in spec["topics"]:
            for i in range(75):
                n += 1
                rng = random.Random(hash((tenant, topic["name"], i)) & 0xFFFFFFFF)
                sents = []
                for tmpl in rng.sample(topic["templates"], k=rng.choice([3, 3, 4])):
                    e = rng.choice(topic["entities"])
                    t = rng.choice(topic["terms"])
                    sents.append(tmpl.format(e=e, t=t))
                if rng.random() < 0.25:
                    sents.insert(rng.randrange(len(sents) + 1), rng.choice(GENERIC_SENTENCES))
                title_term = rng.choice(topic["terms"])
                title_ent = rng.choice(topic["entities"])
                title = f"{topic['name'].title()}: notes on {title_term} with the {title_ent}"
                docs.append(dict(
                    doc_id=f"{tenant[:4]}-{n:04d}",
                    tenant_id=tenant,
                    topic=topic["name"],
                    cluster=topic["cluster"],
                    title=title,
                    body=" ".join(sents),
                ))
    return docs


def make_queries():
    queries = []
    for tenant, spec in TENANTS.items():
        for topic in spec["topics"]:
            terms = topic["terms"]
            for j in range(3):
                t1, t2 = terms[j % len(terms)], terms[(j + 3) % len(terms)]
                q = QUERY_TEMPLATES_KEYWORD[j].format(t1=t1, t2=t2)
                queries.append(dict(tenant_id=tenant, topic=topic["name"],
                                    cluster=topic["cluster"], style="keyword", query=q))
            for j in range(3):
                q = QUERY_TEMPLATES_PARAPHRASE[j].format(topic=topic["name"])
                queries.append(dict(tenant_id=tenant, topic=topic["name"],
                                    cluster=topic["cluster"], style="paraphrase", query=q))
    return queries


if __name__ == "__main__":
    docs = make_docs()
    queries = make_queries()
    with open(os.path.join(HERE, "corpus.jsonl"), "w") as f:
        for d in docs:
            f.write(json.dumps(d) + "\n")
    with open(os.path.join(HERE, "queries.jsonl"), "w") as f:
        for q in queries:
            f.write(json.dumps(q) + "\n")
    print(f"docs={len(docs)} queries={len(queries)}")
    from collections import Counter
    print(Counter(d["tenant_id"] for d in docs))
