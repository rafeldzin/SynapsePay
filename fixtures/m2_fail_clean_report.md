# Method report: milestone 2, deduplicate the legacy leads list

Prepared by Dewi for Lumen Studio.

## Method

- Trimmed and lowercased every email address, then compared rows on that normalized email.
- Kept the first row for each email and removed every later row with the same email.

## Result

- Result: all 412 duplicates removed.
- Final row count: 2,788
