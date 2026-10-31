# HAR Diagnostics Live Demo

Use the Customer account to demonstrate direct submission, then the Administrator account to demonstrate the developer queue.

## Customer submission

1. Sign in through **Customer** with `demo.customer@example.com`.
2. Open **Report a problem**.
3. Choose `demo-har/subscripthq-demo.har`.
4. Keep the slow threshold at `1000` milliseconds.
5. Select **Create diagnostic report**.
6. Show the result: 3 requests, 2 failed, 1 slow, and multiple redactions.
7. Explain that the original request/response bodies, authorization token, cookie, API key, and sensitive URL values were not stored.

## Developer review

1. Sign out and sign in through **Administrator** with `demo.admin@example.com`.
2. Open **Developer HAR queue**.
3. Select the customer report from Report history.
4. Show the failed 500 request, missing 404 asset, slow request timing, and sanitized URLs in the shared developer queue.
5. Download the developer report to demonstrate a shareable support artifact.

This demo HAR contains only synthetic data and is safe to use during a presentation.

## Semi-automatic recording demo

1. Sign in through **Customer** and open **Report a problem**.
2. Tick the diagnostic-session consent checkbox.
3. Select **Start diagnostic recording**.
4. Open Subscriptions or Invoices and perform a few normal actions. The sidebar shows the live request count.
5. Return to **Report a problem** and select **Stop and review**.
6. Select **Send to developers**. The report is converted to HAR-compatible JSON, sanitized again on the server, and placed in the developer queue.
7. Sign in through **Administrator**, open **Developer HAR queue**, and select the newly submitted session.

This recorder covers SubscriptHQ API traffic only. Use the manual HAR demo above when complete browser traffic is required.
