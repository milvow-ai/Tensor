import Ajv from "ajv";
import { describe, expect, it } from "vitest";

import commandSchemas from "../command-schemas.json";
import {
  buildAckAlertPayload,
  buildAddConnectionPayload,
  buildCancelAiJobPayload,
  buildPausePayload,
  buildRemoveConnectionPayload,
  buildResumePayload,
  buildSetBudgetPayload,
  buildSetMaxParallelPayload,
  buildSetMcpToolAccessPayload,
  buildSetPriorityPayload,
  buildSetRoutePayload,
  buildSetStrategyPayload,
  buildSyncMcpToolsPayload,
  buildTestConnectionPayload,
  buildUpdateConnectionPayload,
  createContractExamplePayloads,
} from "../commands";
import type { CommandKind } from "../types";
import fs from "node:fs";
import path from "node:path";

describe("Farm command contract validation", () => {
  const ajv = new Ajv({ allErrors: true, strict: false });
  ajv.addFormat("uuid", /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i);
  const schemas = commandSchemas as Record<string, Record<string, unknown>>;

  const allKinds: CommandKind[] = [
    "pause",
    "resume",
    "set_priority",
    "set_strategy",
    "set_budget",
    "add_connection",
    "update_connection",
    "remove_connection",
    "set_route",
    "test_connection",
    "ack_alert",
    "cancel_ai_job",
    "set_max_parallel",
    "set_mcp_tool_access",
    "sync_mcp_tools",
  ];

  it("exports schemas for all 15 command kinds", () => {
    for (const kind of allKinds) {
      expect(schemas[kind], `Missing exported schema for kind: ${kind}`).toBeDefined();
    }
  });

  const buildersAndInvalidPayloads: Record<
    CommandKind,
    {
      builder: () => Record<string, unknown>;
      invalidPayloads: Record<string, unknown>[];
    }
  > = {
    pause: {
      builder: () => buildPausePayload({ connection_id: "c_contract", reason: "Maintenance" }),
      invalidPayloads: [
        {}, // missing connection_id
        { connection_id: 12345 }, // wrong type
      ],
    },
    resume: {
      builder: () => buildResumePayload({ connection_id: "c_contract" }),
      invalidPayloads: [
        {}, // missing connection_id
        { connection_id: ["not-a-string"] },
      ],
    },
    set_priority: {
      builder: () => buildSetPriorityPayload({ connection_id: "c_contract", priority: 10 }),
      invalidPayloads: [
        { connection_id: "c_contract" }, // missing priority
        { connection_id: "c_contract", priority: -1 }, // priority < 0
        { connection_id: "c_contract", priority: "high" }, // non-integer
      ],
    },
    set_strategy: {
      builder: () => buildSetStrategyPayload({ strategy: "failover", provider_id: "p_contract" }),
      invalidPayloads: [
        {}, // missing strategy
        { strategy: "not_a_real_strategy" }, // invalid enum
      ],
    },
    set_budget: {
      builder: () =>
        buildSetBudgetPayload({
          scope: "provider",
          monthly_usd: 100,
          ref: "p_contract",
          hard_stop: true,
        }),
      invalidPayloads: [
        { monthly_usd: 100 }, // missing scope
        { scope: "invalid_scope", monthly_usd: 100 }, // invalid scope enum
        { scope: "global" }, // missing monthly_usd
        { scope: "global", monthly_usd: -50 }, // negative monthly_usd
      ],
    },
    add_connection: {
      builder: () =>
        buildAddConnectionPayload({
          provider_id: "p_contract",
          id: "c_contract_added",
          auth_ref: "env:CONTRACT_KEY",
          label: "Contract Test Account",
          scope: ["internal"],
          priority: 50,
          concurrency: 2,
          units: {
            credits: {
              limit: 1000,
              period: "month",
              anchor: 1,
              charged_on: "attempt",
              unit_cost_usd: 0.05,
              estimate_per_call: 1,
            },
          },
        }),
      invalidPayloads: [
        { provider_id: "p_contract", id: "c1" }, // missing auth_ref
        { id: "c1", auth_ref: "env:KEY" }, // missing provider_id
        { provider_id: "p_contract", auth_ref: "env:KEY" }, // missing id
        {
          provider_id: "p_contract",
          id: "c1",
          auth_ref: "env:KEY",
          concurrency: 0, // concurrency < 1
        },
        {
          provider_id: "p_contract",
          id: "c1",
          auth_ref: "env:KEY",
          priority: -10, // priority < 0
        },
        {
          provider_id: "p_contract",
          id: "c1",
          auth_ref: "env:KEY",
          units: {
            credits: {
              period: "invalid_period",
            },
          },
        },
      ],
    },
    update_connection: {
      builder: () =>
        buildUpdateConnectionPayload({
          connection_id: "c_contract",
          label: "Updated Label",
          priority: 20,
        }),
      invalidPayloads: [
        {}, // missing connection_id
        { connection_id: 123 }, // wrong type
        { connection_id: "c1", priority: -5 }, // negative priority
      ],
    },
    remove_connection: {
      builder: () => buildRemoveConnectionPayload({ connection_id: "c_contract" }),
      invalidPayloads: [
        {}, // missing connection_id
      ],
    },
    set_route: {
      builder: () =>
        buildSetRoutePayload({
          capability: "verify_email",
          provider_id: "p_contract",
          position: 0,
          enabled: true,
        }),
      invalidPayloads: [
        { capability: "verify_email" }, // missing provider_id
        { provider_id: "p_contract" }, // missing capability
      ],
    },
    test_connection: {
      builder: () => buildTestConnectionPayload({ connection_id: "c_contract" }),
      invalidPayloads: [
        {}, // missing connection_id
      ],
    },
    ack_alert: {
      builder: () => buildAckAlertPayload({ alert_id: "a0000000-0000-0000-0000-000000000001" }),
      invalidPayloads: [
        {}, // missing alert_id
      ],
    },
    cancel_ai_job: {
      builder: () => buildCancelAiJobPayload({ job_id: "00000000-0000-0000-0000-000000000001" }),
      invalidPayloads: [
        {}, // missing job_id
      ],
    },
    set_max_parallel: {
      builder: () => buildSetMaxParallelPayload({ connection_id: "c_contract", max_parallel: 2 }),
      invalidPayloads: [
        { max_parallel: 0 }, // max_parallel < 1
      ],
    },
    set_mcp_tool_access: {
      builder: () => buildSetMcpToolAccessPayload({ provider_id: "p_contract", tool: "query_records", enabled: true }),
      invalidPayloads: [
        { tool: "t1" }, // missing provider_id
        { provider_id: "p1" }, // missing tool
      ],
    },
    sync_mcp_tools: {
      builder: () => buildSyncMcpToolsPayload({ provider_id: "p_contract" }),
      invalidPayloads: [
        { provider_id: 123 }, // wrong type
      ],
    },
  };

  for (const kind of allKinds) {
    describe(`kind: ${kind}`, () => {
      it(`builder output validates against ${kind} JSON Schema`, () => {
        const schema = schemas[kind];
        expect(schema).toBeDefined();
        const validate = ajv.compile(schema);

        const { builder } = buildersAndInvalidPayloads[kind];
        const validPayload = builder();
        const isValid = validate(validPayload);
        if (!isValid) {
          console.error(`Validation errors for ${kind}:`, validate.errors);
        }
        expect(isValid).toBe(true);
      });

      it(`deliberately wrong payloads fail validation for ${kind}`, () => {
        const schema = schemas[kind];
        const validate = ajv.compile(schema);
        const { invalidPayloads } = buildersAndInvalidPayloads[kind];

        for (const wrongPayload of invalidPayloads) {
          const isValid = validate(wrongPayload);
          expect(isValid, `Expected payload to fail validation: ${JSON.stringify(wrongPayload)}`).toBe(false);
        }
      });
    });
  }

  it("every fixture file in __tests__/fixtures/*.json validates against its exported schema", () => {
    const fixturesDir = path.join(__dirname, "fixtures");
    const examplePayloads = createContractExamplePayloads();

    for (const kind of allKinds) {
      const fixtureFile = path.join(fixturesDir, `${kind}.json`);
      expect(fs.existsSync(fixtureFile), `Fixture file missing: ${fixtureFile}`).toBe(true);

      const content = JSON.parse(fs.readFileSync(fixtureFile, "utf-8"));
      const schema = schemas[kind];
      const validate = ajv.compile(schema);

      const isValid = validate(content);
      if (!isValid) {
        console.error(`Fixture validation errors for ${kind}:`, validate.errors);
      }
      expect(isValid).toBe(true);

      // Verify fixture matches the builder output
      expect(content).toEqual(examplePayloads[kind]);
    }
  });
});
