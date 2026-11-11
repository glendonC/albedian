import fs from 'node:fs';
import path from 'node:path';
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import { ObservationSolutionSchema } from '@albedian/contracts';
import type { ActionContext, CelestialFinding, ObservationSession } from '@albedian/contracts';
import { buildActionContext, executeVoiceOrTextCommand, parseUtteranceToAction } from '../voiceParser.js';
import { CATALOG_STAR_NAMES, EVERYDAY_WORD_NAMES } from '../generated/catalogStarNames.js';
import { createSyntheticCalibratedSession, createSyntheticUnresolvedSession } from '../testing/syntheticSessions.js';

/**
 * No sentence is read as being about something it does not name.
 *
 * A sentence that names something acts on that thing or is refused. What is selected, focused or pointed at is
 * taken only when the sentence points at it ("that", "it", "this one", "there") or names nothing at all in a
 * command that needs no name ("explore", "how far?"). Every thing a sentence names must be in this sky: a star or
 * body the name tables know that this sky lacks, or a word that is no known name, pointing word or plain word,
 * stops the sentence, whatever else it names. A refusal says only what the parser can know: that a name of its
 * tables was not found in a sky that was worked out, or that it did not understand a word.
 *
 * The sessions are the solutions the Python backend really writes (tests/fixtures/python_contract_solutions.json),
 * with their own ids and titles: finding-predicted-<body>, finding-predicted-star-<id>, finding-star-<id>.
 * tests/integration/test_voice_named_targets.py judges the backend's parser on the same names and rows, read from
 * SHARED_JSON below, so the two parsers cannot drift.
 */

// ALBEDIAN_FC_SEED pins the seed for the lane's 20-seed runs; unset, fast-check draws one per run.
const SEED = process.env.ALBEDIAN_FC_SEED ? Number(process.env.ALBEDIAN_FC_SEED) : undefined;
const NUM_RUNS = 300;
const NOW = new Date(Date.UTC(2101, 5, 1, 12, 0, 0));

// Strict JSON, read by both parsers' tests (names in other scripts are written as JSON escapes: Sirius in Cyrillic
// letters, Andromeda in Chinese characters). "names": what a person says for each finding of the fixture's
// solutions. "unknown": names neither parser holds. "scripts": names in Cyrillic, Chinese, Arabic and Greek
// letters, which neither holds. "natural": 217 sentences a person who is no astronomer might type about a night
// photo, written by the unit's first auditor; many hold a word neither parser knows. "rows": [solution, selected
// finding or null, what is said, how it is read, and how the backend reads it where that differs]. A reading is
// {"target": id}, {"ask": [ids]}, {"refused": true} or {"action": type}. The backend holds no photo, so it reads a
// name it knows as that name's own record even when the photo's solution does not hold it; the app's reducer then
// refuses it.
const SHARED_JSON = String.raw`
{
  "names": {
    "finding-predicted-moon": ["the Moon", "moon"],
    "finding-predicted-saturn": ["Saturn"],
    "finding-predicted-uranus": ["Uranus"],
    "finding-predicted-star-dabih": ["Dabih"],
    "finding-predicted-star-algedi": ["Algedi"],
    "finding-predicted-star-fomalhaut": ["Fomalhaut"],
    "finding-predicted-star-deneb-algedi": ["Deneb Algedi"],
    "finding-predicted-star-nashira": ["Nashira"],
    "finding-predicted-star-albali": ["Albali"],
    "finding-star-rigel": ["Rigel"],
    "finding-star-betelgeuse": ["Betelgeuse"],
    "finding-star-aldebaran": ["Aldebaran"]
  },
  "unknown": [
    "Andromeda", "the North Star", "the Orion Nebula", "Neptune", "Pluto", "the Pleiades", "Kepler-22b",
    "the Crab Nebula", "the Milky Way", "Halley", "the Big Dipper", "the Ring Nebula"
  ],
  "scripts": ["\u0421\u0438\u0440\u0438\u0443\u0441", "\u5929\u72fc\u661f", "\u0627\u0644\u0642\u0645\u0631", "\u03a3\u03b5\u03bb\u03ae\u03bd\u03b7"],
  "natural": [
    "what's that bright star?", "what is that pretty star?", "what's that really pretty one?",
    "how far away is that one?", "can you tell me what that is?", "could you please tell me about that star?",
    "what am I looking at?", "what's this?", "whats that", "what is that thing near the top?",
    "what's that shiny dot?", "is that a planet?", "is that a satellite?", "is that a plane or a star?",
    "how far is that?", "how far away is it in miles?", "how bright is that?", "how big is that star?",
    "how old is that star?", "how hot is that star?", "what colour is that star?", "what color is that star?",
    "why is that star red?", "why is it so bright?", "is that star still alive?", "is that star dead?",
    "is that the Moon?", "is that Jupiter?", "is that Mars?", "is that Venus?", "is that the North Star?",
    "is that Rigel?", "is that Betelgeuse?", "where is Orion?", "where's the Big Dipper?", "show me Orion's belt",
    "where is Saturn?", "where is the Moon?", "take me there please", "fly me there", "take me to that star",
    "can we go there?", "let's go there", "zoom in on that", "zoom in on that star", "zoom into it",
    "can you zoom in?", "go there", "explore it", "take me to it", "fly to that one", "i want to go to that star",
    "bring me to that star please", "select that star", "select the bright one", "pick that one", "choose that one",
    "focus on that", "focus on that star please", "lock onto that", "look at that", "tell me about it",
    "tell me more about that", "tell me something about that star", "describe that star", "describe it",
    "explain that star", "explain this", "what's behind that star?", "what is beyond that?", "what lies beyond it?",
    "orbit around it", "show me a different angle", "can I see it from the side?",
    "show me another viewpoint of that", "how far is it in light years?", "how many light years away is that?",
    "how far is it from earth?", "how far from us is that?", "how long would it take to get there?",
    "can I see it with my eyes?", "can I see that without a telescope?", "is that visible from here?",
    "when can I see it again?", "hey, what's that?", "um what is that", "uh how far is that", "ok take me there",
    "okay so what's that", "so like what is that star", "wait what is that", "hmm what's that one",
    "oh wow what is that", "omg what is that", "lol what is that", "dude what is that star", "yo what's that",
    "whta is that", "waht is that star", "how fra is that", "tkae me there", "selct that one", "what si that",
    "teh star there", "how far is taht", "pleas take me there", "thx what is that", "plz tell me about it",
    "what's that, mate?", "what's that, love?", "cheers, what is that?", "thanks! how far is it?",
    "awesome, take me there", "amazing, what is that?", "beautiful, what star is that?",
    "gorgeous photo, what's that?", "that's beautiful, what is it?", "this is cool, what's that?",
    "that's so pretty, how far is it?", "the photo is blurry, what is that?", "it's kind of blurry, is that a star?",
    "I took this last night, what's that?", "I took this photo, what is that bright one?",
    "my friend says that's a planet, is it?", "my dad says that is Venus", "my son wants to know what that is",
    "is that star bigger than the sun?", "is it bigger than our sun?", "how does it compare to the sun?",
    "is that a galaxy?", "is that a nebula?", "is that a shooting star?", "is that a UFO?",
    "is that the space station?", "is that a drone?", "is that a cloud?", "is that a light from a city?",
    "what is that orange dot?", "what is that blue star?", "what's that reddish one?", "what's that faint one?",
    "what's that cluster?", "what's that blob?", "what's that streak?", "what is that smudge?",
    "what's the brightest star?", "which one is brightest?", "which star is that?", "which planet is that?",
    "which constellation is that?", "what constellation is this?", "what constellation is that in?",
    "what sign is that?", "is that my zodiac sign?", "show me the sky", "show the sky", "what is in the sky?",
    "what were we looking at?", "identify the stars", "what stars are in my photo?", "what's in my picture?",
    "name the stars", "label the stars", "show me the planets", "where are the planets?", "show the predicted sky",
    "show constellation lines", "go back", "back to photo", "take me back", "back to moment", "stop", "cancel",
    "restore the photo", "clean up the noise", "make the sky clearer", "denoise it", "brighten it",
    "enhance the photo", "compare before and after", "show original", "how far is Rigel?", "take me to Betelgeuse",
    "what is Aldebaran?", "tell me about Fomalhaut", "how far away is Dabih?", "where is Deneb Algedi?",
    "select Nashira", "is Rigel a planet?", "is Rigel hot?", "how hot is Betelgeuse?",
    "is Betelgeuse going to explode?", "how big is Betelgeuse?", "how old is Rigel?",
    "how far is the moon from here?", "how far is the moon right now?", "what phase is the moon?",
    "is the moon full?", "why is the moon orange?", "is that a supermoon?", "can you see Saturn's rings?",
    "how far away is Saturn?", "how big is Saturn?", "is Uranus visible?", "which way is north?", "where is south?",
    "is it a clear night?", "why can't I see more stars?", "why are there so few stars?", "how many stars are there?",
    "how many stars are in this photo?", "is that real?", "is this real or fake?", "did you add that star?",
    "are you sure?", "how sure are you?", "how do you know?"
  ],
  "rows": [
    ["plate_calibrated", "finding-star-rigel", "how far is the Moon?", {"refused": true}, {"target": "finding-predicted-moon"}],
    ["horizon_predicted_only", "finding-predicted-saturn", "how far is the Moon?", {"target": "finding-predicted-moon"}],
    ["horizon_approximate", "finding-predicted-star-dabih", "how far is the Moon?", {"target": "finding-predicted-moon"}],
    ["pose_moon_level_head", "finding-observed-moon", "how far is the Moon?", {"target": "finding-predicted-moon"}],
    ["pose_moon_level_head", "finding-predicted-star-fomalhaut", "take me to the Moon", {"target": "finding-predicted-moon"}],
    ["plate_calibrated", "finding-star-rigel", "take me to Jupiter", {"refused": true}, {"target": "finding-predicted-jupiter"}],
    ["horizon_predicted_only", "finding-predicted-saturn", "take me to Jupiter", {"refused": true}, {"target": "finding-predicted-jupiter"}],
    ["pose_moon_level_head", "finding-predicted-star-fomalhaut", "take me to Saturn", {"target": "finding-predicted-saturn"}],
    ["horizon_predicted_only", "finding-predicted-moon", "take me to Uranus", {"target": "finding-predicted-uranus"}],
    ["horizon_predicted_only", "finding-predicted-saturn", "select the Moon", {"target": "finding-predicted-moon"}],
    ["pose_moon_level_head", "finding-predicted-saturn", "where is Dabih?", {"target": "finding-predicted-star-dabih"}],
    ["pose_moon_level_head", "finding-predicted-saturn", "how far is Deneb Algedi?", {"target": "finding-predicted-star-deneb-algedi"}],
    ["pose_moon_level_head", "finding-predicted-saturn", "tell me about Fomalhaut", {"target": "finding-predicted-star-fomalhaut"}],
    ["pose_moon_level_head", "finding-predicted-saturn", "select Nashira", {"target": "finding-predicted-star-nashira"}],
    ["pose_moon_level_head", "finding-predicted-saturn", "what is Albali?", {"target": "finding-predicted-star-albali"}],
    ["horizon_approximate", "finding-predicted-moon", "select Algedi", {"target": "finding-predicted-star-algedi"}],
    ["plate_calibrated", "finding-star-rigel", "how far is Betelgeuse?", {"target": "finding-star-betelgeuse"}],
    ["plate_calibrated", "finding-star-rigel", "take me to Aldebaran", {"target": "finding-star-aldebaran"}],
    ["plate_calibrated", "finding-star-betelgeuse", "what is Rigel?", {"target": "finding-star-rigel"}],
    ["plate_calibrated_timeless", "finding-star-betelgeuse", "where is Rigel?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-aldebaran", "is Rigel a planet?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-aldebaran", "what constellation is Betelgeuse in?", {"target": "finding-star-betelgeuse"}],
    ["horizon_predicted_only", "finding-predicted-saturn", "is the Moon a planet?", {"target": "finding-predicted-moon"}],

    ["plate_calibrated", "finding-star-rigel", "take me to Andromeda", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "take me to the North Star", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what is the Orion Nebula?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what is a light year?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "go to Neptune", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "fly to the Pleiades", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "tell me about that Andromeda galaxy", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "Andromeda, take me there", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "explore Andromeda", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "how far is Kepler-22b?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what's behind Andromeda?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "orbit Andromeda", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "another viewpoint of Mars", {"refused": true}, {"target": "finding-predicted-mars"}],
    ["plate_too_few_sources", "finding-obs-1", "take me to Andromeda", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "take me to \u0421\u0438\u0440\u0438\u0443\u0441", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "how far is \u4ed9\u5973\u5ea7?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "explore \u00e9toile", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what is that\uff1f", {"target": "finding-star-rigel"}],

    ["plate_calibrated", "finding-star-rigel", "what is that?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "how far away is it?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "take me there", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "that one", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "tell me about it", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "select this one", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "what is that bright one?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "what is that fuzzy patch?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "I ran the solve again, select that one", {"target": "finding-star-rigel"}],
    ["pose_moon_level_head", "finding-observed-moon", "what is that?", {"target": "finding-observed-moon"}],
    ["plate_too_few_sources", "finding-obs-1", "what is that?", {"target": "finding-obs-1"}],
    ["plate_calibrated", "finding-star-rigel", "how far is that star there?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "explore there", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "is there a star? how far", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "how far, are there stars", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "there are stars, how far", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "there's a star, how far", {"refused": true}],
    ["plate_calibrated", null, "what is that?", {"refused": true}],
    ["plate_calibrated", null, "take me there", {"refused": true}],

    ["plate_calibrated", "finding-star-rigel", "explore", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "how far?", {"target": "finding-star-rigel"}],
    ["horizon_predicted_only", "finding-predicted-saturn", "how far?", {"target": "finding-predicted-saturn"}],
    ["plate_calibrated", null, "explore", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "can you explore?", {"target": "finding-star-rigel"}],
    ["plate_calibrated", "finding-star-rigel", "why explore?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what is an orbit?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what are light years?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what is 3d space?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "what should I select?", {"refused": true}],

    ["plate_calibrated", "finding-star-rigel", "how far is the star?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "take me to the planet", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "explore the galaxy", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "fly atlas", {"refused": true}],

    ["horizon_predicted_only", "finding-predicted-moon", "how far is Saturn from Uranus?", {"ask": ["finding-predicted-saturn", "finding-predicted-uranus"]}],
    ["plate_calibrated", "finding-star-aldebaran", "is Rigel brighter than Betelgeuse?", {"ask": ["finding-star-rigel", "finding-star-betelgeuse"]}],

    ["horizon_predicted_only", "finding-predicted-saturn", "how far is Sirius?", {"refused": true}, {"target": "finding-predicted-star-sirius"}],
    ["plate_calibrated", "finding-star-rigel", "how far is the Sun?", {"refused": true}, {"target": "finding-predicted-sun"}],

    ["plate_calibrated", "finding-star-rigel", "how far is Rigel from the Moon?", {"refused": true}, {"ask": ["finding-star-rigel", "finding-predicted-moon"]}],
    ["plate_calibrated", null, "how far is Rigel from the Moon?", {"refused": true}, {"ask": ["finding-star-rigel", "finding-predicted-moon"]}],
    ["plate_calibrated", "finding-star-rigel", "take me to Rigel and Jupiter", {"refused": true}, {"ask": ["finding-star-rigel", "finding-predicted-jupiter"]}],
    ["plate_calibrated", "finding-star-rigel", "how far is Rigel from Sirius?", {"refused": true}, {"ask": ["finding-star-rigel", "finding-star-sirius"]}],
    ["horizon_predicted_only", "finding-predicted-saturn", "take me to Saturn or Mars", {"refused": true}, {"ask": ["finding-predicted-saturn", "finding-predicted-mars"]}],
    ["horizon_predicted_only", "finding-predicted-saturn", "how far is Saturn from Jupiter?", {"refused": true}, {"ask": ["finding-predicted-saturn", "finding-predicted-jupiter"]}],
    ["horizon_predicted_only", "finding-predicted-saturn", "how far is the Moon from Saturn?", {"ask": ["finding-predicted-moon", "finding-predicted-saturn"]}],
    ["plate_calibrated", "finding-star-rigel", "how far is Betelgeuse from Andromeda?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "is Rigel brighter than Andromeda?", {"refused": true}],
    ["plate_calibrated", null, "select Betelgeuse, not Zorblax", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "how far is Rigel from \u0421\u0438\u0440\u0438\u0443\u0441?", {"refused": true}],
    ["plate_calibrated", "finding-star-aldebaran", "Rigel, Betelgeuse and Zorblax", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "show me Rigel and Zorblax", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "is Rigel hot?", {"refused": true}],
    ["plate_calibrated", "finding-star-betelgeuse", "how far is Rigel from Ran?", {"refused": true}, {"ask": ["finding-star-rigel", "finding-gaia-dr3-5164707970261890560"]}],
    ["plate_calibrated", "finding-star-betelgeuse", "I ran the solve again, select Rigel", {"refused": true}, {"ask": ["finding-gaia-dr3-5164707970261890560", "finding-star-rigel"]}],
    ["horizon_predicted_only", "finding-predicted-saturn", "is Saturn brighter than Zorblax?", {"refused": true}],
    ["horizon_predicted_only", "finding-predicted-saturn", "take me to Saturn, not \u5929\u72fc\u661f", {"refused": true}],

    ["plate_calibrated", "finding-star-rigel", "Is that noise or a star?", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "the restored one looks cleaner", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "it looks cleaner now", {"refused": true}],
    ["plate_calibrated", "finding-star-rigel", "restore the video", {"action": "media.restore"}],
    ["plate_calibrated", "finding-star-rigel", "what is in the sky", {"action": "sky.show"}],
    ["plate_calibrated", "finding-star-rigel", "identify the stars", {"action": "sky.show"}],
    ["plate_calibrated", "finding-star-rigel", "show me what was overhead", {"action": "sky.show"}]
  ]
}
`;

type Reading = { target: string } | { ask: string[] } | { refused: true } | { action: string };
interface Shared {
  names: Record<string, string[]>;
  unknown: string[];
  scripts: string[];
  natural: string[];
  rows: Array<[string, string | null, string, Reading, Reading?]>;
}
const SHARED = JSON.parse(SHARED_JSON) as Shared;

const SOLUTIONS = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../../../../tests/fixtures/python_contract_solutions.json'), 'utf8')
) as Record<string, unknown>;
const SAMPLES = Object.keys(SOLUTIONS);
const base = createSyntheticCalibratedSession();

// Each solution is parsed once and never changed, so every session over it holds the same findings, as a session
// in the app does from one request to the next, and no test pays for the same parse a second time
const PARSED = new Map(Object.entries(SOLUTIONS).map(([sample, solution]) => [sample, ObservationSolutionSchema.parse(solution)]));

/** A session over one of the backend's own solutions; the app maps the status to the machine state the same way. */
function sessionOf(sample: string, selected: string | null = null): ObservationSession {
  const solution = PARSED.get(sample)!;
  const calibration =
    solution.status === 'pending' ? 'not_requested' : solution.status === 'approximate' ? 'predicted_only' : solution.status;
  return {
    ...base,
    machines: { ...base.machines, calibration },
    observationSolution: solution,
    selectedFindingId: selected,
    focusedFindingId: null,
  };
}

type Pointer = ActionContext['recentPointer'];

function pointerAt(session: ObservationSession, nearestFindingId: string): Pointer {
  return { xPx: 10, yPx: 20, atTimestampMs: session.currentSourceTimeSec * 1000, viewRevision: session.viewRevision, nearestFindingId };
}

function parse(utterance: string, session: ObservationSession, pointer?: Pointer) {
  return parseUtteranceToAction(utterance, buildActionContext(session, pointer), session, { now: NOW });
}

function readingOf(utterance: string, session: ObservationSession, pointer?: Pointer): Reading {
  const parsed = parse(utterance, session, pointer);
  if (parsed.disambiguation) return { ask: parsed.disambiguation.choices.map((choice) => choice.id) };
  const action = parsed.action as { type: string; findingId?: string; targetFindingId?: string } | undefined;
  if (!action || action.type === 'question.ask') return { refused: true };
  const target = action.findingId ?? action.targetFindingId;
  return target ? { target } : { action: action.type };
}

function findingsOf(sample: string): CelestialFinding[] {
  return sessionOf(sample).observationSolution!.findings;
}

/** No id, class name or contract term: words a person who is not an astronomer reads. */
const NOT_PLAIN = /finding|solution|catalog|ephemeris|observed|predicted|simulated|[a-z]_[a-z]|[a-z]-[a-z]+-[a-z]/i;

describe('@albedian/observation voice parser: the shared rows', () => {
  it('holds a solution for every row and names every finding the backend can name', () => {
    for (const [sample, selected] of SHARED.rows) {
      expect(SAMPLES, sample).toContain(sample);
      if (selected !== null) expect(findingsOf(sample).map((f) => f.id), `${sample} ${selected}`).toContain(selected);
    }
    // Every predicted or catalog-matched finding of the fixture has a spoken name here, and is asked for in a row
    const targets = new Set(SHARED.rows.flatMap(([, , , reads]) => ('target' in reads ? [reads.target] : [])));
    for (const sample of SAMPLES) {
      for (const finding of findingsOf(sample)) {
        if (finding.evidenceClass === 'observed') continue;
        expect(Object.keys(SHARED.names), finding.id).toContain(finding.id);
        expect(targets.has(finding.id), finding.id).toBe(true);
      }
    }
  });

  it.each(SHARED.rows.map(([sample, selected, says, reads]) => ({ sample, selected, says, reads })))(
    '$sample, selected $selected: "$says"',
    ({ sample, selected, says, reads }) => {
      expect(readingOf(says, sessionOf(sample, selected))).toEqual(reads);
    }
  );

  it('never acts on the selection in a row that names something else', () => {
    for (const [sample, selected, says, reads] of SHARED.rows) {
      const read = readingOf(says, sessionOf(sample, selected));
      if ('target' in reads && reads.target === selected) continue;
      expect('target' in read ? read.target : undefined, says).not.toBe(selected ?? 'none');
    }
  });
});

describe('@albedian/observation voice parser: a name that is not found is refused in plain words', () => {
  const rigel = sessionOf('plate_calibrated', 'finding-star-rigel');

  it('says of a word it does not know only that it did not understand it, quoting the word and nothing of the sky', () => {
    const cases: Array<[string, string]> = [
      ['take me to Andromeda', 'andromeda'],
      ['Andromeda, take me there', 'andromeda'],
      // Only the words that stopped the reading are quoted: "galaxy", "star", "big" and "nebula" are words it knows
      ['take me to the Andromeda galaxy', 'andromeda'],
      ['take me to the North Star', 'north'],
      ['take me to the Big Dipper', 'dipper'],
      ['how far is the bright star Zorp?', 'zorp'],
      ["what's behind the Crab Nebula", 'crab'],
      ['take me to the Milky Way', 'milky way'],
      ['how far is Kepler-22b?', 'kepler 22b'],
      ['go to Neptune', 'neptune'],
      // An everyday word is no evidence about the sky either (the auditor's sentences)
      ["that's so pretty, how far is it?", 'pretty'],
      ['what is that pretty star?', 'pretty'],
      ["gorgeous photo, what's that?", 'gorgeous'],
      ['dude what is that star', 'dude'],
      ['selct that one', 'selct'],
      // Beside a name this sky holds, the word is still not understood, and nothing is done about the name
      ['how far is Rigel from Andromeda?', 'andromeda'],
      ['is Rigel brighter than Andromeda?', 'andromeda'],
      ['select Betelgeuse, not Zorblax', 'zorblax'],
      ['how far is Rigel from \u0421\u0438\u0440\u0438\u0443\u0441?', '\u0441\u0438\u0440\u0438\u0443\u0441'],
      ['Rigel, Betelgeuse and Zorblax', 'zorblax'],
      ['is Rigel hot?', 'hot'],
    ];
    const photo = { ...rigel, sourceAsset: { ...rigel.sourceAsset!, mediaKind: 'image' as const } };
    for (const [utterance, said] of cases) {
      for (const session of [rigel, photo, sessionOf('plate_calibrated')]) {
        const parsed = parse(utterance, session);
        expect([utterance, parsed.action, parsed.disambiguation, parsed.reason, parsed.error]).toEqual([
          utterance,
          undefined,
          undefined,
          'ambiguous_target',
          `I did not understand "${said}". Say a star's or a planet's name, or select one and say "that one".`,
        ]);
      }
    }
  });

  it('quotes back at most six words and eighty characters of what it did not understand', () => {
    const notUnderstood = (said: string) =>
      `I did not understand "${said}". Say a star's or a planet's name, or select one and say "that one".`;
    expect(parse('take me to zqa zqb zqc zqd zqe zqf zqg zqh', rigel).error).toBe(notUnderstood('zqa zqb zqc zqd zqe zqf ...'));
    expect(parse('take me to zqa zqb zqc zqd zqe zqf', rigel).error).toBe(notUnderstood('zqa zqb zqc zqd zqe zqf'));
    // Counted in characters, not UTF-16 units: these Cyrillic letters and a letter beyond the basic plane count once each
    for (const letter of ['q', '\u0436', '\u{1D4D0}']) {
      expect(parse(`take me to ${letter.repeat(200)}`, rigel).error, letter).toBe(notUnderstood(`${letter.repeat(80)} ...`));
      expect(parse(`take me to ${letter.repeat(80)}`, rigel).error, letter).toBe(notUnderstood(letter.repeat(80)));
    }
  });

  it('says that a star or a body its tables know was not found in this sky, by the name the table writes', () => {
    const cases: Array<[string, string]> = [
      ['how far is Sirius?', 'Sirius'],
      ['take me to sirius', 'Sirius'],
      ['select SIRIUS', 'Sirius'],
      ['Sirius, take me there', 'Sirius'],
      ['how far is alpha centauri?', 'Rigil Kentaurus'],
      ['how far is the Moon?', 'The Moon'],
      ['how far is moon?', 'The Moon'],
      ['is that the sun?', 'The Sun'],
      ['take me to saturn', 'Saturn'],
      ['take me to Jupiter', 'Jupiter'],
      ['where is venus? select it', 'Venus'],
      ['orbit mars', 'Mars'],
      ["what's behind uranus", 'Uranus'],
      // One of the things named is missing: refused, never read as the one that is there
      ['how far is Rigel from Sirius?', 'Sirius'],
      ['how far is Rigel from the Moon?', 'The Moon'],
      ['take me to Rigel and Jupiter', 'Jupiter'],
      ['Rigel, Betelgeuse and Saturn', 'Saturn'],
      // A name its tables know comes before a word it does not: both are true, and the name says more
      ['how far is Zorblax from Sirius?', 'Sirius'],
    ];
    const photo = { ...rigel, sourceAsset: { ...rigel.sourceAsset!, mediaKind: 'image' as const } };
    for (const [utterance, shown] of cases) {
      for (const [session, kind] of [[rigel, 'video'], [photo, 'photo'], [sessionOf('plate_calibrated'), 'video']] as const) {
        const parsed = parse(utterance, session);
        expect([utterance, parsed.action, parsed.disambiguation, parsed.reason, parsed.error]).toEqual([
          utterance,
          undefined,
          undefined,
          'ambiguous_target',
          `${shown} was not found in this ${kind}'s sky.`,
        ]);
      }
    }
    // On a sky that holds the body, the same words act on it
    const horizon = sessionOf('horizon_predicted_only', 'finding-predicted-uranus');
    expect(readingOf('how far is the Moon?', horizon)).toEqual({ target: 'finding-predicted-moon' });
    expect(parse('how far is the Moon from Mars?', horizon).error).toBe("Mars was not found in this video's sky.");
  });

  it('says a name is missing only of a sky that was worked out, and says so of one that was not', () => {
    // A solve that has not run or did not come out places nothing, so nothing was looked for by name in it. The
    // reason is the unresolved sky where the view marks it so, and otherwise the one a missing name always had
    const notWorkedOut: Array<[string, string]> = [
      ['pending_unsolved', 'ambiguous_target'],
      ['plate_too_few_sources', 'unresolved_sky'],
      ['plate_wcs_failed_verification', 'unresolved_sky'],
    ];
    for (const [sample, reason] of notWorkedOut) {
      for (const utterance of ['how far is Sirius?', 'take me to the Moon', 'select Rigel', 'how far is Rigel from Saturn?']) {
        const parsed = parse(utterance, sessionOf(sample));
        expect([sample, utterance, parsed.action, parsed.reason, parsed.error]).toEqual([
          sample,
          utterance,
          undefined,
          reason,
          'The sky in this video has not been worked out yet, so nothing in it can be found by name.',
        ]);
      }
    }
    // A session with no solve at all, and one with no recording either, say the same
    const { observationSolution: _none, ...unsolved } = sessionOf('pending_unsolved');
    expect(parse('select Betelgeuse', unsolved as ObservationSession).error).toBe(
      'The sky in this video has not been worked out yet, so nothing in it can be found by name.'
    );
    // The app can mark a sky unresolved while the session still holds an older solution
    const stale = { ...sessionOf('plate_calibrated'), machines: { ...sessionOf('plate_too_few_sources').machines } };
    expect(parse('how far is Sirius?', stale).error).toBe(
      'The sky in this video has not been worked out yet, so nothing in it can be found by name.'
    );
    // A solved and a predicted sky were worked out: the name is missing from them
    for (const sample of ['plate_calibrated', 'horizon_approximate', 'horizon_predicted_only', 'pose_moon_level_head']) {
      expect(parse('how far is Sirius?', sessionOf(sample)).error, sample).toBe("Sirius was not found in this video's sky.");
    }
  });

  it('refuses a name this sky does not hold before any other rule reads the sentence, beside a held name or not', () => {
    // A request to seek or to show the sky that also names a star stops at a name that is not here, as it did
    // when it named that star alone; with only names that are here, it is the seek or the sky it asks for
    const cases: Array<[string, string]> = [
      ['seek to 5 seconds and look at Sirius', "Sirius was not found in this video's sky."],
      ['seek to 5 seconds and look at Rigel and Sirius', "Sirius was not found in this video's sky."],
      ['show sky around Rigel and the Moon', "The Moon was not found in this video's sky."],
      ['what is in the sky near Betelgeuse and Saturn?', "Saturn was not found in this video's sky."],
    ];
    for (const [utterance, words] of cases) {
      const parsed = parse(utterance, rigel);
      expect([utterance, parsed.action, parsed.disambiguation, parsed.error]).toEqual([utterance, undefined, undefined, words]);
    }
    expect(readingOf('seek to 5 seconds and look at Rigel', rigel)).toEqual({ action: 'media.seek' });
    expect(readingOf('show sky around Rigel and Betelgeuse', rigel)).toEqual({ action: 'sky.show' });
    // A "how many" question that names them stays a question
    expect(parse('how many stars lie between Rigel and Sirius?', rigel).action?.type).toBe('question.ask');
  });

  it('reads a word of one command beside another command and a name as a word it knows', () => {
    // Each sentence holds a word of a command that the rule which fires does not own ("viewpoint", "angle", "side",
    // "sightline", "different", "distant", "fly", "lies", "along", "shift", "translate"): with every word read, the
    // sentence is about the star it names
    const sentences = [
      'how far is Rigel from this viewpoint?', 'take me to Rigel at an angle', 'take me to the far side of Rigel',
      'how far is the sightline to Rigel?', 'take me to a different star, Rigel', 'take me to distant Rigel',
      'fly me to Rigel', 'Rigel lies there, select it', 'take me along to Rigel', 'shift to Rigel', 'translate to Rigel',
    ];
    for (const said of sentences) {
      expect(readingOf(said, sessionOf('plate_calibrated', 'finding-obs-12')), said).toEqual({ target: 'finding-star-rigel' });
    }
    // The same sentences with a word that is no command's stop there
    expect(parse('how far is Rigel from this zqviewpoint?', rigel).error).toBe(
      'I did not understand "zqviewpoint". Say a star\'s or a planet\'s name, or select one and say "that one".'
    );
  });

  it('reads a star name that is also an everyday word as a name beside another name, with its cue or without', () => {
    // "Ran", "Fang", "Atlas" and "Peacock" are stars of the table. Said with no naming verb and no other name they
    // are everyday words; a sentence that names something is naming things, so beside that name they are names
    // too, and never dropped for it
    const cases: Array<[string, string]> = [
      ['how far is Rigel from Ran?', 'Ran'],
      ['Rigel, Ran', 'Ran'],
      ['is Rigel brighter than Atlas?', 'Atlas'],
      ['select Rigel and Fang', 'Fang'],
      ['I ran the solve again, select Rigel', 'Ran'],
      ['orbit Betelgeuse, not the peacock', 'Peacock'],
      // The first missing name said is the one the refusal writes
      ['how far is the Moon from Ran?', 'The Moon'],
      ['how far is Ran from Sirius?', 'Ran'],
    ];
    for (const [utterance, shown] of cases) {
      const parsed = parse(utterance, rigel);
      expect([utterance, parsed.action, parsed.disambiguation, parsed.reason, parsed.error]).toEqual([
        utterance,
        undefined,
        undefined,
        'ambiguous_target',
        `${shown} was not found in this video's sky.`,
      ]);
    }
    // On a sky that holds the star too, the two names are a question, in the order said
    const [, ranId] = CATALOG_STAR_NAMES.find(([spoken]) => spoken === 'ran')!;
    const template = rigel.observationSolution!.findings.find((f) => f.id === 'finding-star-rigel')!;
    const withRan = {
      ...rigel,
      observationSolution: { ...rigel.observationSolution!, findings: [...rigel.observationSolution!.findings, { ...structuredClone(template), id: ranId, title: 'Ran' }] },
    };
    expect(readingOf('how far is Rigel from Ran?', withRan)).toEqual({ ask: ['finding-star-rigel', ranId] });
    expect(readingOf('I ran the solve again, select Rigel', withRan)).toEqual({ ask: [ranId, 'finding-star-rigel'] });
    // With no other name the word stays an everyday word: it describes, and a pointing word still takes the view
    for (const session of [rigel, { ...withRan, selectedFindingId: 'finding-star-rigel' }]) {
      expect(readingOf('I ran the solve again, select that one', session)).toEqual({ target: 'finding-star-rigel' });
      expect(readingOf('how far is it from Ran?', session)).toEqual({ target: 'finding-star-rigel' });
      expect(parse('how far is ran?', session).error).toBe('That does not say which one. Say its name, or select it and say "that one".');
    }
  });

  it('refuses a southern object or the Southern Cross beside a name this sky holds, in their own words', () => {
    const coalsack = parse('how far is Rigel from the Coalsack?', rigel);
    expect([coalsack.action, coalsack.disambiguation, coalsack.reason]).toEqual([undefined, undefined, 'no_grounded_answer']);
    expect(coalsack.error).toBe(parse('how far is the Coalsack?', rigel).error);
    expect(coalsack.error).toMatch(/^The Coalsack is in the app's deep-sky catalogue/);
    for (const utterance of ['take me to Rigel or Omega Centauri', 'select Betelgeuse and the Jewel Box', 'Rigel, 47 Tuc']) {
      const parsed = parse(utterance, rigel);
      expect([utterance, parsed.action, parsed.disambiguation, parsed.reason]).toEqual([utterance, undefined, undefined, 'no_grounded_answer']);
    }
    // No star of the Cross is in this sky: the same refusal as when the Cross is named alone, never Rigel's distance
    const cross = parse('how far is Rigel from the Southern Cross?', rigel);
    expect([cross.action, cross.disambiguation, cross.reason]).toEqual([undefined, undefined, 'ambiguous_target']);
    expect(cross.error).toBe(parse('how far is the Southern Cross?', rigel).error);
    // With two of its stars in the sky, it asks which of them, as it does when the Cross is named alone
    const template = rigel.observationSolution!.findings.find((f) => f.id === 'finding-star-rigel')!;
    const stars = (['Acrux', 'Mimosa'] as const).map((title, index) => ({ ...structuredClone(template), id: `finding-hip-9100${index}`, title }));
    const southern = { ...rigel, observationSolution: { ...rigel.observationSolution!, findings: [...rigel.observationSolution!.findings, ...stars] } };
    expect(readingOf('how far is Rigel from the Southern Cross?', southern)).toEqual({ ask: ['finding-hip-91000', 'finding-hip-91001'] });
    expect(readingOf('how far is the Southern Cross?', southern)).toEqual({ ask: ['finding-hip-91000', 'finding-hip-91001'] });
  });

  it('gives no ranking of the brightest while the sentence says a name this sky does not hold', () => {
    // The synthetic session carries fluxes measured in its own pixels, and Sirius is the brightest of them
    const measured = { ...base, selectedFindingId: 'finding_rigel' };
    expect(readingOf('take me to the brightest star', measured)).toEqual({ target: 'finding_sirius' });
    expect(readingOf('how far is the brightest star?', measured)).toEqual({ target: 'finding_sirius' });
    // A star named beside it that this sky holds leaves the ranking as it was
    expect(readingOf('is Rigel the brightest star?', measured)).toEqual({ target: 'finding_sirius' });
    const cases: Array<[string, string]> = [
      ['how far is Andromeda from the brightest star?', 'I did not understand "andromeda". Say a star\'s or a planet\'s name, or select one and say "that one".'],
      ['is Zorblax the brightest star?', 'I did not understand "zorblax". Say a star\'s or a planet\'s name, or select one and say "that one".'],
      ['how far is Vega from the brightest star?', "Vega was not found in this video's sky."],
      ['is Saturn brighter than the brightest star?', "Saturn was not found in this video's sky."],
    ];
    for (const [utterance, words] of cases) {
      const parsed = parse(utterance, measured);
      expect([utterance, parsed.action, parsed.disambiguation, parsed.reason, parsed.error]).toEqual([
        utterance,
        undefined,
        undefined,
        'ambiguous_target',
        words,
      ]);
    }
  });

  it('says which words do not tell it which one, and that nothing is selected, without naming any', () => {
    expect(parse('how far is the star?', rigel).error).toBe(
      'That does not say which one. Say its name, or select it and say "that one".'
    );
    expect(parse('select', rigel).error).toBe('That does not say which one. Say its name, or select it and say "that one".');
    const nothing = sessionOf('plate_calibrated');
    for (const utterance of ['what is that?', 'take me there', 'explore', 'how far?']) {
      const parsed = parse(utterance, nothing);
      expect([utterance, parsed.reason, parsed.error]).toEqual([
        utterance,
        'ambiguous_target',
        'Nothing is selected or pointed at. Select a star or planet first, or say its name.',
      ]);
    }
  });

  it('says of an unresolved sky only what is true there: a point of light can still be selected', () => {
    const unresolved = sessionOf('plate_too_few_sources');
    for (const utterance of ['how far is Sirius?', 'select Rigel', 'how far is the Moon?']) {
      const parsed = parse(utterance, unresolved);
      expect([utterance, parsed.action, parsed.reason, parsed.error]).toEqual([
        utterance,
        undefined,
        'unresolved_sky',
        'The sky in this video has not been worked out yet, so nothing in it can be found by name.',
      ]);
    }
    // A word it does not know is not a name it looked for: there, too, it says only that it did not understand
    expect(parse('take me to Andromeda', unresolved).error).toBe(
      'I did not understand "andromeda". Say a star\'s or a planet\'s name, or select one and say "that one".'
    );
    for (const utterance of ['what is that?', 'explore']) {
      const parsed = parse(utterance, unresolved);
      expect([utterance, parsed.action, parsed.reason, parsed.error]).toEqual([
        utterance,
        undefined,
        'unresolved_sky',
        'The sky in this video has not been worked out yet, and nothing is selected or pointed at.',
      ]);
    }
    // What is selected there is still what "that" points at
    expect(readingOf('what is that?', { ...unresolved, selectedFindingId: 'finding-obs-2' })).toEqual({ target: 'finding-obs-2' });
  });

  it('leaves a question that names nothing and points at nothing a question, whatever command word it holds', () => {
    // Each holds a word of a command that, said alone, acts on what is in view; asked, it is about no one thing
    for (const utterance of ['what is an orbit?', 'what are light years?', 'why explore?', 'what should I select?', 'how do I enter space?']) {
      const parsed = parse(utterance, rigel);
      expect([utterance, parsed.action?.type, parsed.error]).toEqual([utterance, 'question.ask', undefined]);
    }
    // Said alone, or asked of what is pointed at, the same words act on what is in view
    for (const utterance of ['orbit', 'explore', 'can you explore?', 'how far?', 'what is that?', 'where does that orbit?']) {
      expect(readingOf(utterance, rigel), utterance).toEqual({ target: 'finding-star-rigel' });
    }
  });

  it('asks which finding under the pointer is meant only of a request that points', () => {
    const overTwo: Pointer = {
      ...pointerAt(rigel, 'finding-star-betelgeuse')!,
      overlappingCandidateIds: ['finding-star-betelgeuse', 'finding-star-aldebaran'],
    };
    for (const utterance of ['take me to Andromeda', 'how far is the Orion Nebula?', 'how far is the star?', 'select the bright one']) {
      const parsed = parse(utterance, rigel, overTwo);
      expect([utterance, parsed.action, parsed.disambiguation, parsed.reason]).toEqual([utterance, undefined, undefined, 'ambiguous_target']);
    }
    for (const utterance of ['what is that?', 'take me there', 'explore']) {
      expect(readingOf(utterance, rigel, overTwo), utterance).toEqual({ ask: ['finding-star-betelgeuse', 'finding-star-aldebaran'] });
    }
  });

  it('asks which one when a sentence names two different things', () => {
    const parsed = parse('how far is Rigel from Betelgeuse?', rigel);
    expect(parsed.action).toBeUndefined();
    expect(parsed.disambiguation).toEqual({
      question: 'Which one did you mean: Rigel or Betelgeuse?',
      choices: [
        { id: 'finding-star-rigel', label: 'Rigel' },
        { id: 'finding-star-betelgeuse', label: 'Betelgeuse' },
      ],
    });
    // The same thing named twice is one thing
    expect(readingOf('Rigel, take me to Rigel', rigel)).toEqual({ target: 'finding-star-rigel' });
  });

  it('writes every refusal of a row in plain words, with no id, class name or contract term', () => {
    for (const [sample, selected, says] of SHARED.rows) {
      const parsed = parse(says, sessionOf(sample, selected));
      if (parsed.error !== undefined) expect(parsed.error, says).not.toMatch(NOT_PLAIN);
    }
  });
});

describe('@albedian/observation voice parser: no refusal says what the parser cannot know', () => {
  // The name a refusal writes for each star and body of the name tables, as it starts a sentence, with its records
  const TABLE = new Map<string, string[]>();
  for (const [, id, shown] of CATALOG_STAR_NAMES) TABLE.set(shown, [...(TABLE.get(shown) ?? []), id]);
  for (const body of ['Sun', 'Moon', 'Venus', 'Mars', 'Jupiter', 'Saturn', 'Uranus']) {
    TABLE.set(body === 'Sun' || body === 'Moon' ? `The ${body}` : body, [`finding-predicted-${body.toLowerCase()}`]);
  }
  // The auditor's three views, then a sky that did not come out, one not solved yet, and a predicted one
  const VIEWS: Array<[string, string | null]> = [
    ['plate_calibrated', 'finding-star-rigel'],
    ['horizon_predicted_only', 'finding-predicted-saturn'],
    ['plate_calibrated', null],
    ['plate_too_few_sources', 'finding-obs-1'],
    ['pending_unsolved', null],
    ['pose_moon_level_head', 'finding-predicted-star-fomalhaut'],
  ];

  it('holds the 217 natural sentences, once each', () => {
    expect(SHARED.natural).toHaveLength(217);
    expect(new Set(SHARED.natural).size).toBe(217);
  });

  it.each(VIEWS)('%s, selected %s: over the natural sentences, the sky is said to lack only a name of the tables', (sample, selected) => {
    const session = sessionOf(sample, selected);
    const held = new Set(session.observationSolution!.findings.map((f) => f.id));
    let refused = 0;
    for (const says of SHARED.natural) {
      const outcome = executeVoiceOrTextCommand(session, says, 'text', undefined, { now: NOW }).outcome;
      if (outcome.status !== 'rejected') continue;
      refused += 1;
      const words = outcome.explanation;
      expect(words, says).not.toMatch(/nothing called/i);
      const lacks = /^(.+) was not found in this (?:photo|video)'s sky\.$/.exec(words);
      if (lacks) {
        // The name is one a table writes, the sky was worked out, and its solution holds no record of that name
        const records = TABLE.get(lacks[1]!);
        expect(records, `${says}: ${words}`).toBeDefined();
        const here = records!.some((id) => held.has(id) || held.has(id.replace(/^finding-/, 'finding-predicted-')));
        expect(here, `${says}: ${words}`).toBe(false);
        expect(['calibrated', 'approximate', 'predicted_only'], `${says}: ${words}`).toContain(session.observationSolution!.status);
      } else if (words !== 'The sky in this video has not been worked out yet, so nothing in it can be found by name.') {
        // No other refusal speaks of what was or was not found
        expect(words, says).not.toMatch(/\bfound\b/i);
      }
    }
    expect(refused).toBeGreaterThan(50);
  });

  it('refuses an everyday word only as a word it did not understand', () => {
    const session = sessionOf('plate_calibrated', 'finding-star-rigel');
    for (const [says, word] of [
      ["that's so pretty, how far is it?", 'pretty'],
      ['omg what is that', 'omg'],
      ['I took this last night, what\'s that?', 'took'],
      ['pleas take me there', 'pleas'],
    ] as const) {
      const outcome = executeVoiceOrTextCommand(session, says, 'text', undefined, { now: NOW }).outcome;
      expect(outcome).toMatchObject({
        status: 'rejected',
        reason: 'ambiguous_target',
        explanation: `I did not understand "${word}". Say a star's or a planet's name, or select one and say "that one".`,
      });
    }
  });
});

describe('@albedian/observation voice parser: the names a solution gives its findings', () => {
  it('reads "Take me to <title>", as the target card says it, as that finding, whatever is selected', () => {
    for (const sample of SAMPLES) {
      const findings = findingsOf(sample);
      for (const finding of findings) {
        // One fixture title holds the word "synthetic", which the unsafe-edit rule refuses before any name is read
        if (/\b(fake|synthetic)\b/i.test(finding.title)) continue;
        for (const selected of [finding.id, findings[0]!.id, null]) {
          const said = `Take me to ${finding.title}`;
          expect(readingOf(said, sessionOf(sample, selected)), `${sample}: ${said}`).toEqual({ target: finding.id });
        }
      }
    }
  });

  it('takes a body or star name as its predicted record, and the measured Moon by pointing or by its whole title', () => {
    // The horizon solver's own titles: "Moon (Observed Sensor Beacon + Ephemeris Lock)" and "Moon (Predicted Ephemeris)"
    const horizon = sessionOf('horizon_approximate', 'finding-predicted-star-dabih');
    expect(readingOf('select the Moon', horizon)).toEqual({ target: 'finding-predicted-moon' });
    expect(readingOf('select Moon (Observed Sensor Beacon + Ephemeris Lock)', horizon)).toEqual({ target: 'finding-observed-moon' });
    expect(readingOf('what is that?', { ...horizon, selectedFindingId: 'finding-observed-moon' })).toEqual({
      target: 'finding-observed-moon',
    });
    // No solution of the fixture holds the Sun, which was below their horizon. The ephemeris writes every body as
    // finding-predicted-<body>, "<Body> (Predicted Ephemeris)" (tests/integration/test_voice_named_targets.py
    // checks the Sun's), so a Sun is made here from the Moon's record
    const moon = horizon.observationSolution!.findings.find((f) => f.id === 'finding-predicted-moon')!;
    const dabih = horizon.observationSolution!.findings.find((f) => f.id === 'finding-predicted-star-dabih')!;
    const made = [
      ...['Sun', 'Venus', 'Mars', 'Jupiter'].map((body) => ({
        ...structuredClone(moon),
        id: `finding-predicted-${body.toLowerCase()}`,
        title: `${body} (Predicted Ephemeris)`,
      })),
      // A predicted star is "<name> (<Bayer designation>)" under finding-predicted-<its catalog id> (predicted_sky.py)
      { ...structuredClone(dabih), id: 'finding-predicted-star-vega', title: 'Vega (Alpha Lyrae)' },
    ];
    const day = { ...horizon, observationSolution: { ...horizon.observationSolution!, findings: [...horizon.observationSolution!.findings, ...made] } };
    for (const said of ['how far is the Sun?', 'select the sun', 'where is the Sun']) {
      expect(readingOf(said, day), said).toEqual({ target: 'finding-predicted-sun' });
    }
    expect(readingOf('take me to Jupiter', day)).toEqual({ target: 'finding-predicted-jupiter' });
    expect(readingOf('how far is Mars?', day)).toEqual({ target: 'finding-predicted-mars' });
    expect(readingOf('select Venus', day)).toEqual({ target: 'finding-predicted-venus' });
    expect(readingOf('where is Vega?', day)).toEqual({ target: 'finding-predicted-star-vega' });
    for (const said of ['how far is the Sun?', 'take me to Jupiter', 'where is Vega?']) {
      expect(readingOf(said, horizon), said).toEqual({ refused: true });
    }
  });

  it('reads a title up to its bracket, so a predicted star of a synthetic session is named by its name', () => {
    const overcast = { ...createSyntheticUnresolvedSession(), selectedFindingId: 'finding_obs_overcast_1' };
    expect(readingOf('where is Vega?', overcast)).toEqual({ target: 'finding_pred_vega' });
    expect(readingOf('take me to Saturn', overcast)).toEqual({ target: 'finding_pred_saturn' });
    // Two findings with one name: the one in view, else a question, never a guess
    const twin = structuredClone(overcast.observationSolution!.findings[1]!);
    const two = {
      ...overcast,
      observationSolution: { ...overcast.observationSolution!, findings: [...overcast.observationSolution!.findings, { ...twin, id: 'finding_pred_saturn_b' }] },
    };
    expect(readingOf('select Saturn', two)).toEqual({ ask: ['finding_pred_saturn', 'finding_pred_saturn_b'] });
    expect(readingOf('select Saturn', { ...two, selectedFindingId: 'finding_pred_saturn_b' })).toEqual({
      target: 'finding_pred_saturn_b',
    });
  });

  it('does not take a one-word catalog abbreviation ("bet", "pi", "51") as a name a person said', () => {
    // The phone-photo solver gives a named star outside the pinned catalog its table abbreviation as its Bayer
    // designation (services/science/phone_photo_solver.py), so a sentence with the word "bet" once named that star
    const template = base.observationSolution!.findings.find((f) => f.id === 'finding_rigel')!;
    const abbreviated = ['bet', 'pi', '51'].map((bayer, index): CelestialFinding => ({
      ...structuredClone(template),
      id: `finding-hip-9000${index}`,
      title: `Unnamed ${index}`,
      catalogRef: { ...template.catalogRef!, bayerDesignation: bayer, catalogSourceId: `HIP 9000${index}` },
    }));
    const session: ObservationSession = {
      ...base,
      selectedFindingId: 'finding_sirius',
      observationSolution: { ...base.observationSolution!, findings: [...abbreviated, ...base.observationSolution!.findings] },
    };
    // "bet" is then a word the parser does not know, so the sentence stops there: it is never the star whose
    // abbreviation it is, and never Sirius beside a word that was not understood
    expect(parse('I bet that one is Sirius', session).error).toBe(
      'I did not understand "bet". Say a star\'s or a planet\'s name, or select one and say "that one".'
    );
    expect(readingOf('I think that one is Sirius', session)).toEqual({ target: 'finding_sirius' });
    expect(readingOf('what do you bet that is?', session)).toEqual({ refused: true });
    expect(readingOf('take me to 51 Pegasi', session)).toEqual({ refused: true });
    expect(readingOf('how far is pi from here?', session)).toEqual({ refused: true });
    // A designation of two words or more is still a name: the whole Bayer name, or a catalogue number
    expect(readingOf('inspect HIP 90001', session)).toEqual({ target: 'finding-hip-90001' });
    expect(readingOf('inspect Beta Orionis', session)).toEqual({ target: 'finding_rigel' });
  });
});

describe('@albedian/observation voice parser: properties over sessions and names', () => {
  const NAMEABLE = SAMPLES.filter((sample) => findingsOf(sample).some((f) => SHARED.names[f.id]));
  const CHATTER_BEFORE = ['', 'please ', 'ok ', 'hey, ', 'can you ', 'i want you to '];
  const CHATTER_AFTER = ['', ' please', ' now', ' again', '?', '.'];
  const NAMING = [
    'take me to {n}', 'fly to {n}', 'go to {n}', 'travel to {n}', 'explore {n}', 'how far is {n}', 'how far away is {n}',
    'the distance to {n}', "what's behind {n}", 'what lies beyond {n}', 'orbit {n}', 'another viewpoint of {n}',
    'select {n}', 'focus on {n}', 'look at {n}', 'inspect {n}', 'tell me about {n}', 'where is {n}', 'what is {n}', '{n}',
  ];
  const NAMING_AND_POINTING = [
    'what is that {n}', 'tell me about that {n}', '{n}, take me there', 'is that {n}? select it', 'how far is it to {n}',
    'take me there, to {n}', 'that one, {n}',
  ];
  const POINTING = [
    'what is that', "what's that", 'take me there', 'fly there', 'how far away is it', 'how far is that star',
    'tell me about it', 'select this one', 'that one', "what's behind it", 'what is that bright one', 'orbit it',
    'select it', 'look at that', 'take me to that one', 'how far is this',
  ];
  const BARE = ['explore', 'orbit', 'how far', 'another viewpoint', 'enter space', "what's behind"];
  const everyday = new Set(EVERYDAY_WORD_NAMES);
  const DESCRIBING = [
    'how far is the star', 'take me to the planet', 'select the bright one', 'explore the galaxy', "what's behind the cluster",
    'orbit the red star', 'take me to a star', 'how far is the nearest galaxy', 'focus on the big blue one',
  ];

  /** A solution of the fixture, with something selected, focused and pointed at, or not. */
  const view = fc
    .constantFrom(...NAMEABLE)
    .chain((sample) => {
      const ids = findingsOf(sample).map((f) => f.id);
      const pick = fc.option(fc.constantFrom(...ids), { nil: null });
      return fc.record({ sample: fc.constant(sample), selected: pick, focused: pick, pointed: pick });
    });
  const chatter = fc.record({ before: fc.constantFrom(...CHATTER_BEFORE), after: fc.constantFrom(...CHATTER_AFTER) });

  function build(v: { sample: string; selected: string | null; focused: string | null; pointed: string | null }) {
    const session = { ...sessionOf(v.sample, v.selected), focusedFindingId: v.focused };
    return { session, pointer: v.pointed ? pointerAt(session, v.pointed) : undefined, inView: v.pointed ?? v.selected ?? v.focused };
  }

  it('PBT: a sentence that names a finding acts on that finding, whatever is selected, focused or pointed at', () => {
    const named = view.chain((v) => {
      const nameable = findingsOf(v.sample).filter((f) => SHARED.names[f.id]);
      return fc.constantFrom(...nameable).chain((finding) =>
        fc.record({ v: fc.constant(v), id: fc.constant(finding.id), name: fc.constantFrom(...SHARED.names[finding.id]!) })
      );
    });
    fc.assert(
      fc.property(named, fc.constantFrom(...NAMING, ...NAMING_AND_POINTING), chatter, ({ v, id, name }, template, c) => {
        const { session, pointer } = build(v);
        const said = `${c.before}${template.replace('{n}', name)}${c.after}`;
        expect(readingOf(said, session, pointer), said).toEqual({ target: id });
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          // The two sentences the lane was opened for, on a solution that holds the body, with a star selected
          [{ v: { sample: 'pose_moon_level_head', selected: 'finding-predicted-star-fomalhaut', focused: null, pointed: null }, id: 'finding-predicted-moon', name: 'the Moon' }, 'how far is {n}', { before: '', after: '?' }],
          [{ v: { sample: 'pose_moon_level_head', selected: 'finding-predicted-star-fomalhaut', focused: null, pointed: null }, id: 'finding-predicted-saturn', name: 'Saturn' }, 'take me to {n}', { before: '', after: '' }],
          // A two-word name whose words are names too, and the measured Moon in view while the Moon is named
          [{ v: { sample: 'pose_moon_level_head', selected: 'finding-predicted-star-algedi', focused: null, pointed: null }, id: 'finding-predicted-star-deneb-algedi', name: 'Deneb Algedi' }, '{n}, take me there', { before: '', after: '' }],
          [{ v: { sample: 'horizon_approximate', selected: 'finding-observed-moon', focused: 'finding-observed-moon', pointed: 'finding-observed-moon' }, id: 'finding-predicted-moon', name: 'moon' }, 'what is that {n}', { before: '', after: '?' }],
        ],
      }
    );
  });

  // The two properties around these cases, once for every name the backend holds (bar the everyday words, which
  // name a star only when cued) in every template: on a sky that lacks the star nothing is acted on, and on one
  // that holds a finding with the star's own id that finding is, with a detection selected that no name stands for.
  // One case per template, on two sessions built once: the fixture's solved sky, and the same sky holding a finding
  // for every star of the table at once, so each name must also be told from every other name.
  // Measured on this laptop with --maxWorkers=2, ten runs one after another, uptime's one-minute load read before
  // each: at a load of 25.6 to 32.4 the slowest of these cases took 74 to 264 ms, and the slowest case of the whole
  // file 77 to 264 ms. Each case has the default 5000 ms, a fifth of which is 1000 ms. As one case, the first
  // version's same sentences took 2.7 to 3.2 s here at a load of 13 to 14, and 6.1 to 7.1 s, past the timeout, in
  // the audit at a load of 15 to 22.
  const starry = (() => {
    const lacking = sessionOf('plate_calibrated', 'finding-obs-12');
    const solution = lacking.observationSolution!;
    const held = new Set(solution.findings.map((f) => f.id));
    const table = CATALOG_STAR_NAMES.filter(([spoken]) => !everyday.has(spoken));
    const added = [...new Set(table.map(([, id]) => id))]
      .filter((id) => !held.has(id))
      .map((id) => ({ ...structuredClone(solution.findings[0]!), id, title: 'Unnamed' }));
    const holding = { ...lacking, observationSolution: { ...solution, findings: [...solution.findings, ...added] } };
    return { lacking, holding, held, table };
  })();

  it('holds every star name of the table for the cases below', () => {
    expect(starry.table.length).toBeGreaterThan(400);
    expect(starry.table.length * (NAMING.length + NAMING_AND_POINTING.length)).toBeGreaterThan(10_000);
    expect(starry.holding.observationSolution!.findings.length).toBeGreaterThan(400);
  });

  it.each([...NAMING, ...NAMING_AND_POINTING])('reads every star name of the table in "%s" as its own finding, or as nothing', (template) => {
    for (const [spoken, id] of starry.table) {
      const said = template.replace('{n}', spoken);
      expect(readingOf(said, starry.holding), said).toEqual({ target: id });
      if (!starry.held.has(id)) expect(readingOf(said, starry.lacking), said).toEqual({ refused: true });
    }
  });

  it('PBT: a sentence that names two findings asks which, and never picks the one in view', () => {
    const pair = view.chain((v) => {
      const nameable = findingsOf(v.sample).filter((f) => SHARED.names[f.id]);
      return fc.record({
        v: fc.constant(v),
        a: fc.constantFrom(...nameable),
        b: fc.constantFrom(...nameable),
      });
    });
    fc.assert(
      fc.property(pair, fc.constantFrom('how far is {a} from {b}', 'take me to {a} or {b}', 'select {a} and {b}', '{a}, {b}'), ({ v, a, b }, template) => {
        fc.pre(a.id !== b.id);
        const { session, pointer } = build(v);
        const said = template.replace('{a}', SHARED.names[a.id]![0]!).replace('{b}', SHARED.names[b.id]![0]!);
        expect(readingOf(said, session, pointer), said).toEqual({ ask: [a.id, b.id] });
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  // A name neither parser holds: one from the list, a made-up word, or a star or body this solution lacks
  const madeUp = fc.stringMatching(/^zq[a-z]{3,8}$/);
  const strangerTo = (sample: string) => {
    const session = sessionOf(sample);
    const held = new Set(session.observationSolution!.findings.map((f) => f.id));
    const absentStars = CATALOG_STAR_NAMES.filter(
      ([spoken, id]) => !everyday.has(spoken) && !held.has(id) && !held.has(id.replace(/^finding-/, 'finding-predicted-'))
    ).map(([spoken]) => spoken);
    const absentBodies = ['Jupiter', 'Mars', 'Venus', 'the Sun', 'Saturn', 'Uranus', 'the Moon'].filter(
      (body) => !held.has(`finding-predicted-${body.replace('the ', '').toLowerCase()}`)
    );
    return fc.oneof(fc.constantFrom(...SHARED.unknown), madeUp, fc.constantFrom(...absentStars), fc.constantFrom(...absentBodies));
  };

  it('PBT: a sentence that names something this sky does not hold is refused, even when it also points', () => {
    const stranger = view.chain((v) => fc.record({ v: fc.constant(v), name: strangerTo(v.sample) }));
    fc.assert(
      fc.property(stranger, fc.constantFrom(...NAMING, ...NAMING_AND_POINTING), chatter, ({ v, name }, template, c) => {
        const { session, pointer } = build(v);
        const said = `${c.before}${template.replace('{n}', name)}${c.after}`;
        expect(readingOf(said, session, pointer), said).toEqual({ refused: true });
      }),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          [{ v: { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: null, pointed: null }, name: 'the Moon' }, 'how far is {n}', { before: '', after: '?' }],
          [{ v: { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: null, pointed: null }, name: 'Jupiter' }, 'take me to {n}', { before: '', after: '' }],
          [{ v: { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: 'finding-star-rigel', pointed: 'finding-star-rigel' }, name: 'Andromeda' }, 'tell me about that {n}', { before: '', after: '' }],
          [{ v: { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: null, pointed: null }, name: 'Andromeda' }, '{n}, take me there', { before: '', after: '' }],
          [{ v: { sample: 'horizon_predicted_only', selected: 'finding-predicted-saturn', focused: null, pointed: null }, name: 'vega' }, 'where is {n}', { before: '', after: '?' }],
          [{ v: { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: null, pointed: null }, name: 'zqxyz' }, '{n}', { before: '', after: '' }],
        ],
      }
    );
  });

  // What one name of a sentence is to this sky: a finding it holds, a star or body the name tables know that it
  // does not hold, or a word no table holds (from the list, made up, or in another script)
  type Said = { kind: 'held'; says: string; id: string } | { kind: 'known'; says: string; shown: string } | { kind: 'unknown'; says: string };
  const BODIES: Record<string, string> = { sun: 'The Sun', moon: 'The Moon', venus: 'Venus', mars: 'Mars', jupiter: 'Jupiter', saturn: 'Saturn', uranus: 'Uranus' };
  // The words of each listed unknown name that neither parser reads, which is what a refusal quotes of it
  const UNREAD: Record<string, string> = {
    'the North Star': 'north', 'the Orion Nebula': 'orion', 'the Pleiades': 'pleiades', 'Kepler-22b': 'kepler 22b', 'the Crab Nebula': 'crab',
    'the Milky Way': 'milky way', 'the Big Dipper': 'dipper', 'the Ring Nebula': 'ring',
  };
  const SAID = new Map<string, fc.Arbitrary<Said>>();
  const saidIn = (sample: string): fc.Arbitrary<Said> => {
    if (!SAID.has(sample)) SAID.set(sample, namesFor(sample));
    return SAID.get(sample)!;
  };
  const namesFor = (sample: string): fc.Arbitrary<Said> => {
    const findings = findingsOf(sample);
    const here = new Set(findings.map((f) => f.id));
    const lacks = (id: string) => !here.has(id) && !here.has(id.replace(/^finding-/, 'finding-predicted-'));
    const held = fc
      .constantFrom(...findings.filter((f) => SHARED.names[f.id]))
      .chain((f) => fc.constantFrom(...SHARED.names[f.id]!).map((says): Said => ({ kind: 'held', says, id: f.id })));
    const star = fc
      .constantFrom(...CATALOG_STAR_NAMES.filter(([spoken, id]) => !everyday.has(spoken) && lacks(id)))
      .map(([spoken, , shown]): Said => ({ kind: 'known', says: spoken, shown }));
    const body = fc
      .constantFrom(...Object.keys(BODIES).filter((name) => lacks(`finding-${name}`)))
      .map((name): Said => ({ kind: 'known', says: name === 'sun' || name === 'moon' ? `the ${name}` : name, shown: BODIES[name]! }));
    const unknown = fc
      .oneof(fc.constantFrom(...SHARED.unknown), fc.constantFrom(...SHARED.scripts), madeUp)
      .map((says): Said => ({ kind: 'unknown', says }));
    return fc.oneof({ weight: 3, arbitrary: held }, star, body, { weight: 2, arbitrary: unknown });
  };
  const PAIRS = [
    'take me to {a} or {b}', 'fly to {a} and {b}', 'go to {a}, not {b}', 'how far is {a} from {b}', 'the distance from {a} to {b}',
    "what's behind {a} and {b}", 'orbit {a} and {b}', 'select {a} and {b}', 'look at {a}, not {b}', 'tell me about {a} and {b}',
    '{a}, {b}', 'is {a} brighter than {b}',
  ];
  const TRIPLES = [
    'take me to {a}, {b} or {c}', 'how far is {a} from {b} and {c}', "what's behind {a}, {b} or {c}", 'orbit {a}, {b} and {c}',
    'select {a}, {b} and {c}', '{a}, {b}, {c}', 'is {a} brighter than {b} or {c}',
  ];
  type Viewed = { sample: string; selected: string | null; focused: string | null; pointed: string | null };

  /** Every name said must be held: then one thing is acted on or several are asked about, else nothing is done. */
  function everyNameIsHeldOrNothingIsDone(v: Viewed, names: Said[], template: string): void {
    const { session, pointer } = build(v);
    const said = names.reduce((text, name, index) => text.replace(`{${'abc'[index]}}`, name.says), template);
    const parsed = parse(said, session, pointer);
    const reading = readingOf(said, session, pointer);
    const missing = names.find((name) => name.kind === 'known');
    const unread = names.find((name) => name.kind === 'unknown');
    if (!missing && !unread) {
      const ids = [...new Set(names.map((name) => (name as { id: string }).id))];
      expect(reading, said).toEqual(ids.length === 1 ? { target: ids[0] } : { ask: ids });
      return;
    }
    // Never an action on, an answer about, or a question between the names this sky does hold
    expect(reading, said).toEqual({ refused: true });
    expect(parsed.disambiguation, said).toBeUndefined();
    if (missing) {
      expect([said, parsed.action, parsed.error]).toEqual([said, undefined, `${missing.shown} was not found in this video's sky.`]);
    } else if (parsed.action) {
      // Names alone, none of them one this sky or the tables hold, are no command: a question, about no finding
      expect([said, parsed.action.type, names.every((name) => name.kind === 'unknown')]).toEqual([said, 'question.ask', true]);
      expect(/^(?:is )?\{a\}/.test(template), said).toBe(true);
    } else {
      const quoted = (UNREAD[unread!.says] ?? unread!.says.toLowerCase()).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      expect(parsed.error, said).toMatch(
        new RegExp(`^I did not understand "${quoted}(?: [^"]+)?"\\. Say a star's or a planet's name, or select one and say "that one"\\.$`, 'u')
      );
    }
  }

  it('PBT: of two names, one that this sky does not hold stops the sentence, whatever the command', () => {
    const two = view.chain((v) => fc.record({ v: fc.constant(v), names: fc.tuple(saidIn(v.sample), saidIn(v.sample)) }));
    const rigel: Viewed = { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: null, pointed: null };
    const saturn: Viewed = { sample: 'horizon_predicted_only', selected: 'finding-predicted-saturn', focused: null, pointed: null };
    fc.assert(
      fc.property(two, fc.constantFrom(...PAIRS), ({ v, names }, template) => everyNameIsHeldOrNothingIsDone(v, names, template)),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          // The auditor's sentences: a body, a made-up word, a name of the list and a name in Cyrillic beside a held name
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'known', says: 'the Moon', shown: 'The Moon' }] }, 'how far is {a} from {b}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'known', says: 'Jupiter', shown: 'Jupiter' }] }, 'fly to {a} and {b}'],
          [{ v: saturn, names: [{ kind: 'held', says: 'Saturn', id: 'finding-predicted-saturn' }, { kind: 'known', says: 'Mars', shown: 'Mars' }] }, 'take me to {a} or {b}'],
          [{ v: saturn, names: [{ kind: 'held', says: 'Saturn', id: 'finding-predicted-saturn' }, { kind: 'known', says: 'Jupiter', shown: 'Jupiter' }] }, 'how far is {a} from {b}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Betelgeuse', id: 'finding-star-betelgeuse' }, { kind: 'unknown', says: 'Andromeda' }] }, 'how far is {a} from {b}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'unknown', says: 'Andromeda' }] }, 'is {a} brighter than {b}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Betelgeuse', id: 'finding-star-betelgeuse' }, { kind: 'unknown', says: 'Zorblax' }] }, 'look at {a}, not {b}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'unknown', says: '\u0421\u0438\u0440\u0438\u0443\u0441' }] }, 'how far is {a} from {b}'],
          // The missing name said first, a missing star, and two names that are both held
          [{ v: rigel, names: [{ kind: 'unknown', says: 'Zorblax' }, { kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }] }, '{a}, {b}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'known', says: 'sirius', shown: 'Sirius' }] }, 'orbit {a} and {b}'],
          [{ v: saturn, names: [{ kind: 'held', says: 'the Moon', id: 'finding-predicted-moon' }, { kind: 'held', says: 'Saturn', id: 'finding-predicted-saturn' }] }, 'how far is {a} from {b}'],
        ],
      }
    );
  });

  it('PBT: of three names, one that this sky does not hold stops the sentence, whatever the command', () => {
    const three = view.chain((v) =>
      fc.record({ v: fc.constant(v), names: fc.tuple(saidIn(v.sample), saidIn(v.sample), saidIn(v.sample)) })
    );
    const rigel: Viewed = { sample: 'plate_calibrated', selected: 'finding-star-rigel', focused: null, pointed: null };
    fc.assert(
      fc.property(three, fc.constantFrom(...TRIPLES), ({ v, names }, template) => everyNameIsHeldOrNothingIsDone(v, names, template)),
      {
        seed: SEED,
        numRuns: NUM_RUNS,
        examples: [
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'held', says: 'Betelgeuse', id: 'finding-star-betelgeuse' }, { kind: 'unknown', says: 'Zorblax' }] }, '{a}, {b}, {c}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'known', says: 'the Moon', shown: 'The Moon' }, { kind: 'held', says: 'Aldebaran', id: 'finding-star-aldebaran' }] }, 'select {a}, {b} and {c}'],
          [{ v: rigel, names: [{ kind: 'held', says: 'Rigel', id: 'finding-star-rigel' }, { kind: 'held', says: 'Betelgeuse', id: 'finding-star-betelgeuse' }, { kind: 'held', says: 'Aldebaran', id: 'finding-star-aldebaran' }] }, 'how far is {a} from {b} and {c}'],
          [{ v: rigel, names: [{ kind: 'unknown', says: '\u5929\u72fc\u661f' }, { kind: 'held', says: 'Betelgeuse', id: 'finding-star-betelgeuse' }, { kind: 'known', says: 'vega', shown: 'Vega' }] }, 'take me to {a}, {b} or {c}'],
        ],
      }
    );
  });

  it('PBT: a sentence that only points acts on what is pointed at, then selected, then focused, or is refused', () => {
    fc.assert(
      fc.property(view, fc.constantFrom(...POINTING), chatter, (v, pointing, c) => {
        const { session, pointer, inView } = build(v);
        const said = `${c.before}${pointing}${c.after}`;
        expect(readingOf(said, session, pointer), said).toEqual(inView ? { target: inView } : { refused: true });
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: a command that names nothing at all acts on what is in view, or is refused', () => {
    fc.assert(
      fc.property(view, fc.constantFrom(...BARE), chatter, (v, bare, c) => {
        const { session, pointer, inView } = build(v);
        const said = `${c.before}${bare}${c.after}`;
        expect(readingOf(said, session, pointer), said).toEqual(inView ? { target: inView } : { refused: true });
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });

  it('PBT: a sentence that describes something without naming it or pointing at it is refused', () => {
    fc.assert(
      fc.property(view, fc.constantFrom(...DESCRIBING), chatter, (v, describing, c) => {
        const { session, pointer } = build(v);
        const said = `${c.before}${describing}${c.after}`;
        expect(readingOf(said, session, pointer), said).toEqual({ refused: true });
      }),
      { seed: SEED, numRuns: NUM_RUNS }
    );
  });
});

describe('@albedian/observation voice parser: request ids', () => {
  it('gives each request its own id, even the same words again after a refusal', () => {
    const rigel = sessionOf('plate_calibrated', 'finding-star-rigel');
    const ids: string[] = [];
    let session = rigel;
    // A question is refused and leaves the view's revision as it was; a selection moves it on
    for (const utterance of ['hello there', 'hello there', 'what is that?', 'what is that?', 'hello there']) {
      const heard = executeVoiceOrTextCommand(session, utterance, 'text', undefined, { now: NOW });
      const requestId = heard.voiceActionRecord.proposedAction?.requestId;
      expect(requestId, utterance).toEqual(expect.any(String));
      ids.push(requestId!);
      session = heard.session;
    }
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe('@albedian/observation reducer: two sentences that say only what the session holds', () => {
  const run = (session: ObservationSession, utterance: string) =>
    executeVoiceOrTextCommand(session, utterance, 'text', undefined, { now: NOW });

  it('says which variant is now shown after "restore", and claims no restoration, people or audio it cannot show', () => {
    // The synthetic overcast session holds no restoration, so nothing is shown and the refusal says so plainly
    const overcast = createSyntheticUnresolvedSession();
    const original = run(overcast, 'restore the video');
    expect(original.outcome).toMatchObject({ status: 'rejected' });
    expect(JSON.stringify(original.outcome)).toContain('No restored version of this recording exists yet.');
    const natural = run(base, 'restore the video');
    expect(natural.outcome).toMatchObject({
      status: 'completed',
      spokenFeedback: 'Now showing the restored version. Its audio is copied from the original recording.',
    });
    // A variant that does not carry the source's audio, with the packet hashes ingest stored, makes no audio claim
    const silent = { ...base, mediaVariants: base.mediaVariants.map((v) => ({ ...v, audioStreams: [] })) };
    expect(run(silent, 'restore the video').outcome).toMatchObject({
      status: 'completed',
      spokenFeedback: 'Now showing the restored version.',
    });
    for (const result of [original, natural]) {
      expect(JSON.stringify(result.outcome)).not.toMatch(/Restored sky clarity|preserving people/);
    }
  });

  it('says of a sky it cannot show only what the session holds, in plain words, never the solver\'s own warning', () => {
    const ask = 'What were we looking at?';
    // Words of the solver, the contract or a status: none belongs in a sentence a person reads
    const SOLVERS_WORDS = /WCS|registration|unresolved|calibrat|solution|held-out|gate|status|pending|insufficient|timestamp/i;
    const said = (session: ObservationSession) => {
      const outcome = run(session, ask).outcome;
      expect(outcome).toMatchObject({ status: 'rejected', reason: 'unresolved_sky' });
      const refused = outcome as Extract<typeof outcome, { status: 'rejected' }>;
      expect(JSON.stringify([refused.explanation, refused.nextActions])).not.toMatch(SOLVERS_WORDS);
      expect(JSON.stringify([refused.explanation, refused.nextActions])).not.toMatch(NOT_PLAIN);
      for (const warning of session.observationSolution?.warnings ?? []) expect(JSON.stringify(outcome)).not.toContain(warning);
      return [refused.explanation, refused.nextActions];
    };

    // A solve from the stars that did not come out, on a recording that carries its time and place: the session
    // holds the solver's warning, and the sentence says only that the sky could not be worked out
    for (const sample of ['plate_too_few_sources', 'plate_wcs_failed_verification']) {
      const unresolved = sessionOf(sample);
      expect(unresolved.observationSolution!.warnings.length).toBeGreaterThan(0);
      expect(said(unresolved)).toEqual(["This video's sky could not be worked out from its stars.", []]);
      const photo = { ...unresolved, sourceAsset: { ...unresolved.sourceAsset!, mediaKind: 'image' as const } };
      expect(said(photo)).toEqual(["This photo's sky could not be worked out from its stars.", []]);
    }

    // The same solve on a recording that lacks its time, its place or both: it says which, and only then
    const unresolved = sessionOf('plate_too_few_sources');
    const { capturedAtUTC: _dropped, ...timeless } = unresolved.sourceAsset!;
    expect(said({ ...unresolved, sourceAsset: timeless })).toEqual([
      "This video's sky could not be worked out from its stars. The video does not say when it was taken.",
      [],
    ]);
    const { observerLocation: _nowhere, ...placeless } = unresolved.sourceAsset!;
    expect(said({ ...unresolved, sourceAsset: placeless })).toEqual([
      "This video's sky could not be worked out from its stars. The video does not say where it was taken.",
      [],
    ]);
    const { observerLocation: _gone, ...bare } = timeless;
    expect(said({ ...unresolved, sourceAsset: bare })).toEqual([
      "This video's sky could not be worked out from its stars. The video does not say when or where it was taken.",
      [],
    ]);

    // No solve at all: it has not been worked out yet, which is all the session shows
    const { observationSolution: _none, ...unsolved } = unresolved;
    expect(said(unsolved as ObservationSession)).toEqual(["This video's sky has not been worked out yet.", []]);
    expect(said({ ...(unsolved as ObservationSession), sourceAsset: bare })).toEqual([
      "This video's sky has not been worked out yet. The video does not say when or where it was taken.",
      [],
    ]);
    // No recording either: there is no sky to speak of, and the next step is to bring one
    const { sourceAsset: _noAsset, ...empty } = unsolved;
    expect(said(empty as ObservationSession)).toEqual([
      'There is no photo or video here yet, so there is no sky to show.',
      ['Import a recording first'],
    ]);
    // The app marks the sky unresolved while it still holds a solve that came out: that solve's warnings explain
    // another state, and the session does not show a failed solve, so neither is said
    const stale = { ...sessionOf('plate_calibrated'), machines: { ...unresolved.machines } };
    expect(stale.observationSolution!.warnings.length).toBeGreaterThan(0);
    expect(said(stale)).toEqual(["This video's sky has not been worked out yet.", []]);
  });
});
