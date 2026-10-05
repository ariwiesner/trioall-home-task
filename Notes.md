**Roughly how long i spent**
Approximately 8 hours.
i really enjoyed working on the assignment and would have been happy to spend more time on itr. However, during these two days i was also balancing a few interviews, which limited the time i could dedicate to it.

**Decisions i made that Summer didn’t ask for, and why**
1. What types of files can we upload ? CSV, Excel, or both?
Decision: Support both CSV and Excel files.
Summer receives files from multiple types of loggers and does not specify a single file format. Supporting both CSV and Excel makes the ingestion flow more robust and avoids unnecessarily restricting the input format.

2. How do we handle different date formats?
Decision: All supported date formats are normalized into a common datetime representation. Dates that cannot be parsed are marked as invalid and are not used for temperature analysis.

3. How do we handle ERR and other invalid readings? mark as invalid input? ignore it?
Decision: invalid readings are kept in the system but are excluded from analysis.
if a critical field such as the timestamp or temperature is invalid, the entire row is marked as invalid.
Non-critical invalid fields are stored as null/invalid while keeping the rest of the row.
invalid rows are visible to the user so data quality issues are not silently hidden.

4. How do we know which logger belongs to which fridge and branch?
Decision: Each logger is mapped to a refrigerator through a time-based assignment.
* if the uploaded file has a Logger-iD column, it's used as the source of truth for every row - this is what the sample sheet format already has, so most uploads need no extra input.
* The upload form also lets the user pick a logger, but that's only used as a fallback for files with NO Logger-iD column at all (e.g. a raw two-column time+temp export).
* Either way, Branch/Fridge text in the file itself is never read — the system uses the logger mapping (not the file's own branch/fridge columns) to associate measurements with the correct fridge and branch.
* This keeps the upload process simple while allowing logger-to-fridge mappings to change over time, and avoids trusting messy/inconsistent branch or fridge spelling in the source file.

5. What happens when a logger moves to another fridge like she said that already happened?
Decision: Logger-to-fridge assignments are time-based.
* When a logger is moved to another fridge, a new assignment is created instead of overwriting the previous one.
* Each assignment has a start date and an optional end date.
* Measurements are associated with the fridge based on the logger assignment that was active at the measurement timestamp.
* This preserves historical data when a logger is moved between fridges.

6. What temperature unit does each logger use, and how do we normalize it?
Decision: All temperatures are normalized to Celsius, which is the standard unit used by the system.
* Each logger has a configured temperature unit (Celsius or Fahrenheit).
* incoming temperature values are converted to Celsius during ingestion before being stored and analyzed.

7. How long / under what conditions does a temperature reading become a problem?
Decision: A temperature above 5°C is considered abnormal.
* A single short spike above 5°C that returns to normal in the next reading is treated as a short spike rather than a sustained temperature alert.
* A sustained temperature above 5°C for 30 minutes or more is reported as a temperature alert.
* We also detect a gradual warming trend and report it separately, even before the temperature exceeds 5°C.
* The 30-minute threshold is an initial assumption and should be confirmed with the client before production.

8. How long without a reading is considered a data gap?
Decision: Expected interval = 15 minutes. A data gap is reported whenever an expected reading is missing based on the logger's configured sampling interval. The system does not assume the reason for the missing data.

9. What should be shown on the main dashboard? number of branches? of fridges?
Decision: Show each branch and the refrigerators belonging to it.
For each branch, show how many refrigerators are Good, Problem, or Needs Review.
Show the same summary across all branches at the top of the dashboard.
Problem means a clear issue was detected, such as sustained temperature above 5°C.
Needs Review is used for suspicious or incomplete data, such as data gaps or a gradual warming trend.
From the dashboard, the user can drill down into a specific branch and then into an individual refrigerator to see more details.

10. What happens if the same file is uploaded twice? and how to avoid it?
Decision: Duplicate uploads should not create duplicate measurements. Exact duplicate files are detected using a file hash, and each measurement is uniquely identified by logger iD and timestamp.

11. Do we save the original uploaded file or the converted file?
Decision: i decided to keep the original uploaded files for traceability and debugging.
The original file is kept unchanged, while the parsed and normalized measurements are stored separately and used for analysis. This makes it possible to investigate unexpected data later without losing the original input.

12. How do we detect gradual warming?
Decision: The system detects gradual warming by looking for a consistent upward temperature trend across multiple consecutive readings.
A refrigerator can be marked as Needs Review even before reaching 5°C if a clear warming trend is detected. A single increase or small fluctuations are not considered a warming trend.
The exact thresholds for detecting a warming trend are initial assumptions and should be validated with the client before production.

13. What happens if an uploaded file contains an unknown logger?
Decision: Unknown loggers are not rejected during upload. Their readings are stored as unresolved. Once the logger is added to the registry and assigned to a refrigerator through the Django admin, the system automatically reprocesses the unresolved readings via Django post-save signals. invalid rows remain invalid and are not reprocessed.
For production, i would consider whether this implicit signal-based behavior should be replaced or complemented by an explicit background job, especially as ingestion volume grows.

**What i’d want to ask Summer before this goes live**
1. is 5°C the correct threshold for all refrigerator types, or can it vary by refrigerator/product category?

2. is 15 minutes the expected interval for all loggers, or should this be configurable per logger?

3. who should have access to the system in production, and are different permission levels needed for managers, operators, and administrators?

4. should temperature problems or prolonged missing data trigger notifications (email/SMS/etc.), or is the dashboard intended to be the only way users are alerted?

5. when readings are missing, should we treat them as a technical/data issue, or could there be legitimate reasons such as maintenance, logger replacement, or manual shutdown? 

**What’s not done, and what i’d do with one more hour**
1. Temperature visualization: Temperature history is currently presented as a list of breach, warming, and gap events. A temperature chart with the 5°C threshold would make gradual warming and temperature breaches easier to identify.
2. invalid row visibility: invalid rows are stored and counted, but there is no dedicated user-facing view for Summer to inspect the specific rejected rows and the reasons they were rejected.
3. Production readiness: Authentication and asynchronous/background processing would be needed before using the system in a production environment at larger scale.

With one more hour, i would prioritize:
1. improving the temperature history visualization.
2. Adding a simple per-branch status summary to make the dashboard easier to scan.

**How i worked with my Ai tools**
i used two Ai tools in different parts of the assignment.
i started with ChatGPT because it is the model i have been working with since my time in the army, so it already has context about my technical background and knowledge level. i used it mainly during the product and architecture phase. We discussed the assignment, identified the decisions that Summer had not specified, and i proposed my own decisions. We then challenged those decisions together and discussed the trade-offs before i started implementing.

For the implementation, i worked with Claude Code. i intentionally worked with it incrementally rather than asking it to build the entire application at once. i started with the architecture and implementation plan, and then asked it to build one part at a time based on the decisions i had already made so i could keep up with it.

One issue i caught during this process was related to Fahrenheit readings. The system converts all temperatures to Celsius for analysis, but i also wanted to show the original value to the user if the source logger uses Fahrenheit. An initial implementation used the logger's current unit configuration to reconstruct the original value in the frontend. i rejected this approach because the logger's configuration can change over time. For example, a reading that was originally recorded as 38.3°F should still be displayed as 38.3°F even if that logger is later configured to use Celsius.

The fix was to treat the unit as part of the reading at ingestion time: the system stores the raw temperature together with the unit that was used for that reading, in addition to the normalized Celsius value used for analysis. 

The relevant implementation and tests are in the `readings` app.
