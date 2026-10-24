import { expect, test } from "@playwright/test";

test.describe("Dashboard sign-in and sealed secrets", () => {
  test("device-code card shows the code and link", async ({ page }) => {
    await page.goto("/pools/ai/codex");

    // Find the codex-01 row which needs login
    const row = page.getByTestId("account-row").filter({ hasText: "codex-01" });
    await expect(row).toBeVisible();

    // Click Sign in button in the StatusCell
    const signInBtn = row.getByTestId("account-signin-button");
    await expect(signInBtn).toBeVisible();
    await signInBtn.click();

    // Verify Sign in drawer opens
    const drawer = page.getByTestId("sign-in-drawer");
    await expect(drawer).toBeVisible();

    // Verify device code is displayed
    const codeEl = drawer.getByTestId("device-code");
    await expect(codeEl).toBeVisible();
    await expect(codeEl).toHaveText("WDJB-MJGN");

    // Verify verification URI / auth link
    const link = drawer.getByTestId("auth-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "https://auth.openai.com/codex/device");
    await expect(link).toHaveAttribute("target", "_blank");

    // Verify note about private window
    await expect(
      drawer.getByText("Use a private window if this browser is signed in to another account"),
    ).toBeVisible();
  });

  test("paste-code flow sends a submit_login_input whose payload contains an envelope and not the plaintext", async ({
    page,
  }) => {
    await page.goto("/pools/ai/claude");

    // Find the claude-02 row which needs login (paste-code session)
    const row = page.getByTestId("account-row").filter({ hasText: "claude-02" });
    await expect(row).toBeVisible();

    const signInBtn = row.getByTestId("account-signin-button");
    await expect(signInBtn).toBeVisible();
    await signInBtn.click();

    const drawer = page.getByTestId("sign-in-drawer");
    await expect(drawer).toBeVisible();

    // Verify link to provider auth
    const link = drawer.getByTestId("auth-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "https://claude.ai/oauth/authorize?client_id=claude-cli");

    const secretCode = "super-secret-auth-code-xyz987";
    let capturedPostData = "";

    page.on("request", (req) => {
      const post = req.postData();
      if (post) {
        capturedPostData += `${post}\n`;
      }
    });

    const input = drawer.getByTestId("input-code");
    await expect(input).toBeVisible();
    await input.fill(secretCode);

    const connectBtn = drawer.getByTestId("connect-button");
    await expect(connectBtn).toBeEnabled();
    await connectBtn.click();

    // Plaintext input must be cleared immediately after sending
    await expect(input).toHaveValue("");

    // State updates to checking / verifying
    await expect(drawer.getByText("Checking…")).toBeVisible();

    // Plaintext code MUST NOT be in any network payload
    expect(capturedPostData).not.toContain(secretCode);

    // Sealed envelope markers MUST be present in the submitted command
    expect(capturedPostData).toContain("submit_login_input");
    expect(capturedPostData).toContain("ECDH-ES+HKDF-SHA256+A256GCM");
  });

  test("duplicate card shows the reason", async ({ page }) => {
    await page.goto("/pools/ai/claude");

    // claude-03 has session-duplicate in fixtures
    const row = page.getByTestId("account-row").filter({ hasText: "claude-03" });
    await expect(row).toBeVisible();

    // Open row actions menu
    const actionsBtn = row.getByRole("button", { name: /Actions for/i });
    await expect(actionsBtn).toBeVisible();
    await actionsBtn.click();

    // Click Sign in again
    const signInAgainItem = page.getByRole("menuitem", { name: "Sign in again" });
    await expect(signInAgainItem).toBeVisible();
    await signInAgainItem.click();

    const drawer = page.getByTestId("sign-in-drawer");
    await expect(drawer).toBeVisible();

    const card = drawer.getByTestId("sign-in-card");
    await expect(card).toBeVisible();

    // Verify duplicate reason and actions
    await expect(card.getByText("Duplicate account")).toBeVisible();
    await expect(card.getByText("same account as claude-01")).toBeVisible();
    await expect(card.getByRole("button", { name: "Try again in a private window" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Remove this slot" })).toBeVisible();
  });

  test("add-accounts dialog sends add_accounts", async ({ page }) => {
    await page.goto("/pools/ai/claude");

    // Click + Add accounts button in pool header
    const addBtn = page.getByTestId("add-accounts-button");
    await expect(addBtn).toBeVisible();
    await addBtn.click();

    const dialog = page.getByTestId("add-accounts-dialog");
    await expect(dialog).toBeVisible();

    let capturedPostData = "";
    page.on("request", (req) => {
      const post = req.postData();
      if (post) {
        capturedPostData += `${post}\n`;
      }
    });

    // Submit dialog
    const submitBtn = dialog.getByTestId("add-accounts-submit");
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    // Verify add_accounts command was sent
    await expect.poll(() => capturedPostData.includes("add_accounts")).toBeTruthy();

    // Dialog transitions to stack view showing sign-in cards
    await expect(dialog.getByText("Sign in your new accounts")).toBeVisible();
    await expect(dialog.getByTestId("sign-in-card")).toBeVisible();
  });

  test("bulk API keys send one save_secret per key with no plaintext in any payload", async ({ page }) => {
    await page.goto("/pools/reoon");

    const form = page.getByTestId("api-key-form");
    await expect(form).toBeVisible();

    // Select bulk keys tab
    const bulkTab = form.getByRole("tab", { name: "Paste keys, one per line" });
    await expect(bulkTab).toBeVisible();
    await bulkTab.click();

    const key1 = "reoon-secret-key-alpha-111";
    const key2 = "reoon-secret-key-beta-222";

    const bulkTextarea = form.getByTestId("api-key-bulk-input");
    await expect(bulkTextarea).toBeVisible();
    await bulkTextarea.fill(`${key1}\n${key2}`);

    let capturedPostData = "";
    page.on("request", (req) => {
      const post = req.postData();
      if (post) {
        capturedPostData += `${post}\n`;
      }
    });

    const saveBtn = form.getByTestId("save-secret-button");
    await expect(saveBtn).toBeEnabled();
    await saveBtn.click();

    // Plaintext input is cleared immediately
    await expect(bulkTextarea).toHaveValue("");

    // Verify result rows appear and show Saved
    const resultRows = form.getByTestId("secret-result-row");
    await expect(resultRows).toHaveCount(2);
    await expect(resultRows.nth(0)).toContainText("Saved");
    await expect(resultRows.nth(1)).toContainText("Saved");

    // Plaintext keys MUST NOT appear anywhere in the request payloads
    expect(capturedPostData).not.toContain(key1);
    expect(capturedPostData).not.toContain(key2);

    // Sealed envelope and save_secret command MUST be in payloads
    expect(capturedPostData).toContain("save_secret");
    expect(capturedPostData).toContain("ECDH-ES+HKDF-SHA256+A256GCM");
  });
});
