import { expect, test } from "@playwright/test";

test.describe("Perplexity AI pool and sealed API key sign-in", () => {
  test("add a Perplexity account -> sign-in card asks for API key -> submitting sends save_secret with sealed envelope", async ({
    page,
  }) => {
    await page.goto("/pools/ai/perplexity");

    // Click + Add accounts button in pool header
    const addBtn = page.getByTestId("add-accounts-button");
    await expect(addBtn).toBeVisible();
    await addBtn.click();

    const dialog = page.getByTestId("add-accounts-dialog");
    await expect(dialog).toBeVisible();

    // Verify Perplexity provider is selected by default
    const providerSelect = dialog.getByTestId("provider-select");
    await expect(providerSelect).toHaveValue("perplexity");

    let capturedPostData = "";
    page.on("request", (req) => {
      const post = req.postData();
      if (post) {
        capturedPostData += `${post}\n`;
      }
    });

    // Submit dialog to add account
    const submitBtn = dialog.getByTestId("add-accounts-submit");
    await expect(submitBtn).toBeVisible();
    await submitBtn.click();

    // Verify add_accounts command was sent
    await expect.poll(() => capturedPostData.includes("add_accounts")).toBeTruthy();

    // Dialog transitions to stack view showing sign-in cards
    await expect(dialog.getByText("Sign in your new accounts")).toBeVisible();
    const card = dialog.getByTestId("sign-in-card");
    await expect(card).toBeVisible();

    // Verify auth link points to Perplexity API keys
    const authLink = card.getByTestId("auth-link");
    await expect(authLink).toBeVisible();
    await expect(authLink).toHaveAttribute("href", "https://www.perplexity.ai/account/api/keys");
    await expect(authLink).toHaveAttribute("target", "_blank");

    // Verify password field for Perplexity API key
    await expect(card.getByText("Perplexity API key", { exact: true })).toBeVisible();
    const input = card.getByTestId("input-code");
    await expect(input).toBeVisible();
    await expect(input).toHaveAttribute("type", "password");

    const secretKey = "pplx-secret-test-key-xyz987";
    await input.fill(secretKey);

    const connectBtn = card.getByTestId("connect-button");
    await expect(connectBtn).toBeEnabled();
    await connectBtn.click();

    // Plaintext input must be cleared immediately after sending
    await expect(input).toHaveValue("");

    // State updates to checking / verifying
    await expect(card.getByText("Checking…")).toBeVisible();

    // Plaintext key MUST NOT appear anywhere in the request payloads
    expect(capturedPostData).not.toContain(secretKey);

    // Sealed envelope and save_secret command MUST be in payloads
    expect(capturedPostData).toContain("save_secret");
    expect(capturedPostData).toContain("ECDH-ES+HKDF-SHA256+A256GCM");
  });

  test("existing needs-login Perplexity account sign-in action asks for API key and saves sealed secret", async ({
    page,
  }) => {
    await page.goto("/pools/ai/perplexity");

    // Find the perplexity-02 row which needs login
    const row = page.getByTestId("account-row").filter({ hasText: "perplexity-02" });
    await expect(row).toBeVisible();

    const signInBtn = row.getByTestId("account-signin-button");
    await expect(signInBtn).toBeVisible();
    await signInBtn.click();

    const drawer = page.getByTestId("sign-in-drawer");
    await expect(drawer).toBeVisible();

    // Verify auth link
    const authLink = drawer.getByTestId("auth-link");
    await expect(authLink).toBeVisible();
    await expect(authLink).toHaveAttribute("href", "https://www.perplexity.ai/account/api/keys");

    // Verify password field
    await expect(drawer.getByText("Perplexity API key", { exact: true })).toBeVisible();
    const input = drawer.getByTestId("input-code");
    await expect(input).toBeVisible();
    await expect(input).toHaveAttribute("type", "password");

    const secretKey = "pplx-standby-key-456";
    let capturedPostData = "";
    page.on("request", (req) => {
      const post = req.postData();
      if (post) {
        capturedPostData += `${post}\n`;
      }
    });

    await input.fill(secretKey);
    const connectBtn = drawer.getByTestId("connect-button");
    await expect(connectBtn).toBeEnabled();
    await connectBtn.click();

    // Plaintext input must be cleared immediately after sending
    await expect(input).toHaveValue("");

    // Checking… visible
    await expect(drawer.getByText("Checking…")).toBeVisible();

    // Plaintext key MUST NOT appear in network payload
    expect(capturedPostData).not.toContain(secretKey);

    // Sealed envelope markers and save_secret present
    expect(capturedPostData).toContain("save_secret");
    expect(capturedPostData).toContain("ECDH-ES+HKDF-SHA256+A256GCM");
  });
});
