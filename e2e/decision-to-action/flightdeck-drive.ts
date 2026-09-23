// Flight Deck driver for the decision-to-action E2E.
//
// Runs inside the rbx-flightdeck checkout (FD_DIR) with Bun, against the
// disposable flightdeck_test database. It reuses the repository's own service
// functions and integration fixture so the E2E exercises the same code the
// worker endpoint calls, without a browser session:
//
//   seed     create workspace, Workstream, cycle, candidate, ACT decision,
//            standing authorization (claude_code_design) and approve the Action
//   stage    run one worker stage (source_event | maestro_admission |
//            dispatch | materialize | activate | reconcile) once
//   state    print the intent row
//
// Usage: bun run flightdeck-drive.ts <seed|stage <name>|state> (env below)
//   TEST_DATABASE_URL, PP_URL, PP_SERVICE_KEY, MAESTRO_URL, MAESTRO_ADMIT_KEY,
//   MAESTRO_DISPATCH_KEY, MAESTRO_TARGET_ENVIRONMENT, PP_PROFILE_ID,
//   E2E_REPO, E2E_SOURCE_COMMIT, E2E_STATE (json file shared between calls)
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { makeFixture, entriesForTotal, testSql } from '@fd/tests/integration/helpers';
import { ensureCurrentCycle } from '@fd/src/lib/server/services/missions';
import { createCandidate, scoreCandidate } from '@fd/src/lib/server/services/candidates';
import { decide } from '@fd/src/lib/server/services/decisions';
import { approveAction } from '@fd/src/lib/server/services/actions';
import {
	admitNextPublicPresenceMission,
	configurePublicPresenceAutopilot,
	deliverNextPublicPresenceSourceEvent
} from '@fd/src/lib/server/services/publicPresence';
import {
	activateNextPublicPresenceMission,
	decideDispatchByPolicy,
	materializeNextPublicPresenceJob,
	reconcileNextPublicPresenceMission
} from '@fd/src/lib/server/services/publicPresenceActivation';

const env = (k: string, d = ''): string => process.env[k] ?? d;
const stateFile = env('E2E_STATE', '/tmp/fd-e2e-state.json');
const sql = testSql();

function loadState(): Record<string, string> {
	return existsSync(stateFile) ? JSON.parse(readFileSync(stateFile, 'utf8')) : {};
}
function saveState(s: Record<string, string>) {
	writeFileSync(stateFile, JSON.stringify(s, null, 2));
}

async function seed() {
	const fx = await makeFixture(sql);
	const workstream = await fx.createMission('e2e-presence-autopilot', 'RBX_INTERNAL');
	const { defs } = await fx.createPolicy(workstream.id);
	await fx.createCapacity(workstream.id, null, 10);
	const cycle = await ensureCurrentCycle(sql, fx.auth, workstream);
	const candidate = await createCandidate(sql, fx.auth, workstream, {
		cycleId: cycle.id,
		title: 'E2E: publish the sovereign loop note',
		proposedAction: 'Turn the sovereign loop lesson into one public post produced by the creative mission.'
	});
	await scoreCandidate(sql, fx.auth, workstream, candidate.id, entriesForTotal(defs, 90));
	const { actionId } = await decide(sql, fx.auth, workstream, {
		cycleId: cycle.id,
		candidateId: candidate.id,
		kind: 'ACT',
		justification: 'E2E: inside the standing authorization envelope.'
	});
	const now = new Date();
	const authorization = await configurePublicPresenceAutopilot(sql, fx.auth, workstream, {
		identitySlug: env('PP_IDENTITY_SLUG', 'psyctl'),
		profileId: env('PP_PROFILE_ID'),
		seriesKey: env('PP_SERIES_KEY', 'signal'),
		formatKey: env('PP_FORMAT_KEY', 'instagram.single_image'),
		adapter: 'claude_code_design',
		repository: env('E2E_REPO', 'rbxrobotica/rbx-creatives'),
		sourceCommit: env('E2E_SOURCE_COMMIT'),
		maxIntents: 4,
		maxSpendCents: 2000,
		validFrom: new Date(now.getTime() - 60_000),
		validUntil: new Date(now.getTime() + 6 * 3_600_000),
		authorizedAt: now
	});
	const approved = await approveAction(sql, fx.auth, workstream, { actionId: actionId!, at: now });
	const state = {
		workstreamId: workstream.id,
		workspaceId: fx.workspaceId,
		actionId: actionId!,
		authorizationId: authorization.authorizationId,
		intentId: approved.publicPresenceIntentId as string,
		actorEmail: fx.auth.email as string
	};
	saveState(state);
	console.log(JSON.stringify({ seeded: true, ...state }));
}

async function stage(name: string) {
	const pp = { baseUrl: env('PP_URL'), serviceKey: env('PP_SERVICE_KEY') };
	const targetEnvironment = env('MAESTRO_TARGET_ENVIRONMENT', 'test');
	let result: unknown;
	switch (name) {
		case 'source_event':
			result = await deliverNextPublicPresenceSourceEvent(sql, pp);
			break;
		case 'maestro_admission':
			result = await admitNextPublicPresenceMission(sql, {
				baseUrl: env('MAESTRO_URL'),
				admissionKey: env('MAESTRO_ADMIT_KEY'),
				targetEnvironment
			});
			break;
		case 'materialize':
			result = await materializeNextPublicPresenceJob(sql, {
				publicPresenceBaseUrl: pp.baseUrl,
				serviceKey: pp.serviceKey,
				targetEnvironment
			});
			break;
		case 'dispatch':
			result = await decideDispatchByPolicy(sql, { targetEnvironment });
			break;
		case 'activate':
			result = await activateNextPublicPresenceMission(sql, {
				maestroBaseUrl: env('MAESTRO_URL'),
				dispatchKey: env('MAESTRO_DISPATCH_KEY'),
				targetEnvironment
			});
			break;
		case 'reconcile':
			result = await reconcileNextPublicPresenceMission(sql, {
				maestroBaseUrl: env('MAESTRO_URL'),
				admissionKey: env('MAESTRO_ADMIT_KEY'),
				publicPresenceBaseUrl: pp.baseUrl,
				serviceKey: pp.serviceKey,
				targetEnvironment
			});
			break;
		default:
			throw new Error(`unknown stage ${name}`);
	}
	console.log(JSON.stringify({ stage: name, result }));
}

async function state() {
	const s = loadState();
	const [row] = await sql`
		select i.id, i.state, i.last_error_code, i.maestro_mission_ref, i.maestro_request_fingerprint,
		       i.public_presence_source_event_id, i.public_presence_content_item_id,
		       i.public_presence_content_version_id, i.dispatch_approval_id, i.maestro_lease_id,
		       i.terminal_outcome, i.job_id, i.attempts
		from public_presence_execution_intent i where i.id = ${s.intentId}`;
	const approvals = await sql`
		select decided_by_kind, policy_reason, revoked_at from execution_dispatch_approval
		where intent_id = ${s.intentId}`;
	console.log(JSON.stringify({ intent: row, approvals }));
}

const [cmd, arg] = process.argv.slice(2);
try {
	if (cmd === 'seed') await seed();
	else if (cmd === 'stage') await stage(arg);
	else if (cmd === 'state') await state();
	else throw new Error('usage: seed | stage <name> | state');
} finally {
	await sql.end();
}
