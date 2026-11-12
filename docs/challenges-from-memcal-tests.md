# Challenge ideas grounded in MemCal regression tests

This is an authoring catalog, not benchmark results or a claim that MemCal is better. The tests encode failures the product guards against; many inject structured model replies or stored rows and therefore do **not** establish end-to-end extraction accuracy. Convert the behavior into lossless synthetic messages and use each provider's real lifecycle.

## Scope and evidence

Reviewed the complete inventory of **92 top-level Python test modules** at MemCal commit `dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a`: parsed every test definition and module description, then inspected behavior-bearing fixtures/assertions for the challenge families below. The coverage ledger at the end records every module, including ones that are operational rather than narrative. This is systematic candidate discovery, not a line-by-line proof of every assertion. Source links pin the reviewed revision; no tests were executed or modified for this documentation task.

All examples below are synthetic. Test references sometimes retain existing fixture names; scenario authors should use a new synthetic cast and avoid copying personal details. The requested next-day-reply case is a proposed combination of tested prior-topic restoration and attendance transitions, not a claim that one existing test implements the whole scenario.

## How to turn an idea into a fair test

- Write the event/state transitions first, including who knows an update and when. Generate or supply the actual messages afterward, and judge against what those messages really establish.
- Ask at selected intermediate times as well as in a fresh session after final ingestion. Silence, conditional replies and later corrections need intermediate checkpoints; a final-only recap misses them.
- Freeze explicit independently assessable criteria before providers run. Complete rosters/counts need completeness criteria; do not replace them with independent inclusion checks that allow invented guests.
- Keep messages, timestamps, senders, thread membership and exposure identical. Do not expose this catalog, private event ledger or expected facts to providers.
- Preserve uncertainty where the source is ambiguous. Do not reward an arbitrary guess merely because it matches an author's unstated intent.
- Separate semantic accuracy, retained-memory/source-assisted paths, delivery correctness, readiness and infrastructure failure. A correct answer after opening source is not the same measurement as successful retention.
- Pair each difficult variant with a simple control. Vary one axis first; combine challenges only after the isolated behavior is understood.
- Respect native completion/flush semantics. Missing ingestion or context delivery is not a memory-quality loss. Record actual context and tools, including timeouts, retries, fallback and background work.

## Suggested first challenge set

Use C01–C13 as a compact seven-day core, not all in one giant conversation. Add C14, C29 and C30 as separate integration qualification checks. C15–C28 broaden personal memory and temporal realism; C31–C35 are optional follow-on coverage. Keep persona/event families as independent units when reporting results rather than treating many questions about one dinner as independent evidence.

### C01 · P0 · Reply after a quiet night

**Type:** memory. **Scenario:** Monday evening the owner invites Rowan to Friday dinner. Rowan sends nothing until Tuesday morning, then replies “Friday works.” Ask once before that reply and once after ingestion.

**Check:** Before: invited/unconfirmed, not declined or accepted. After: confirmed for the original Friday, not Tuesday. Preserve thread context across nightly processing.

**Regression evidence:** [test_dream_usability.py::TestGroupSchedulingKeepsConversationContext.test_short_date_reply_gets_prior_topic_and_silent_members](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_dream_usability.py#L164); [test_event_semantic_context.py::TestStructuredAttendance.test_later_acceptance_wins_over_earlier_conditional_role](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L96).

### C02 · P0 · Conditional attendance is not acceptance

**Type:** memory. **Scenario:** Rowan says “I can come if my shift ends early.” Later they explicitly accept; another guest was confirmed throughout.

**Check:** Keep the other guest; conditional is not confirmed until the later acceptance. Test intermediate and final states.

**Regression evidence:** [test_event_semantic_context.py::TestStructuredAttendance.test_inviting_nick_preserves_mom_and_conditional_reply_is_not_attendance](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L35); [test_event_semantic_context.py::TestStructuredAttendance.test_a_later_acceptance_replaces_the_generated_conditional_note](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L73).

### C03 · P0 · One guest withdraws; the outing survives

**Type:** memory. **Scenario:** Three people agree to lunch. Rowan withdraws; the other two say they are still going.

**Check:** Exactly the remaining roster; one active outing, not an event cancellation. Replay older confirmations without restoring Rowan.

**Regression evidence:** [test_attendee_updates.py::TestCitedRosterCorrections.test_withdrawal_replaces_cited_roster_without_cancelling_other_plans](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_attendee_updates.py#L39); [test_attendee_updates.py::TestCitedRosterCorrections.test_merge_does_not_union_a_withdrawn_guest_back_into_the_roster](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_attendee_updates.py#L77).

### C04 · P0 · Partial roster versus complete correction

**Type:** memory. **Scenario:** A message says “Taylor is joining too.” Later a complete roster says “It is just us and Morgan now.”

**Check:** The first message adds; the second replaces when the source really states a complete list. Grade exact roster, including exclusion of invented guests.

**Regression evidence:** [test_attendee_updates.py::TestCitedRosterCorrections.test_partial_or_uncited_roster_remains_additive](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_attendee_updates.py#L66); [test_attendee_updates.py::TestCitedRosterCorrections.test_cited_empty_roster_can_remove_the_last_guest](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_attendee_updates.py#L73).

### C05 · P0 · Same title, distinct occasions

**Type:** memory. **Scenario:** Arrange dinner with Rowan at North Cafe at 6 and dinner with Morgan at South Cafe at 7 on the same day. Correct only the first.

**Check:** Two plans with correct people/time/place; correction does not mutate the other event. Include a same-title next-week variant.

**Regression evidence:** [test_separate_occasions.py::TestDistinctGuestsAndDetailsDoNotCollapse.test_source_apply_replay_and_typed_correction_keep_two_occasions](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_separate_occasions.py#L25); [test_merge.py::TestClustering.test_two_poker_nights_a_week_apart_stay_separate](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_merge.py#L77).

### C06 · P0 · Cross-channel partial updates

**Type:** memory. **Scenario:** A group arranges noon lunch at North Cafe. A DM moves the time; an email later changes only the venue. Explicitly relay updates where participants need to know them.

**Check:** One event with latest supported time and venue; no duplicate outing and no restoration of an old field during later ingestion.

**Regression evidence:** [test_cancellation_linking.py::TestMergedEvidenceHonorsLiveCorrections.test_cross_bundle_time_changes_without_restoring_an_old_location](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_cancellation_linking.py#L477); [test_regressions.py::TestABbqAndABlockPartyBbqWereTwoRows.test_the_email_moves_the_row_the_group_chat_wrote](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L1506).

### C07 · P0 · Late delivery of old evidence

**Type:** memory. **Scenario:** Deliver a Sunday confirmation after a Monday correction. Include written time and collection time separately.

**Check:** Monday correction wins even though Sunday source arrived later. Do not move the simulated calendar to ingestion time.

**Regression evidence:** [test_daytime_part3.py::TestNewActivityNominatesWithoutChangingFacts.test_a_late_old_message_is_new_with_its_old_date](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part3.py#L98); [test_daytime_part3.py::TestSourceBackedPrecedence.test_older_source_cannot_undo_a_newer_settlement](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part3.py#L533).

### C08 · P0 · Fresh time with stale address

**Type:** memory. **Scenario:** Old message names North Cafe; a newer source moves to South Cafe. Later deliver a message that newly changes only the time while quoting the old address.

**Check:** New time plus South Cafe. Each field follows its own evidence; a fresh wrapper cannot lend freshness to stale quoted content.

**Regression evidence:** [test_field_evidence.py::TestFieldSourcesPartialApply.test_old_address_plus_newer_time_partial_result](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_field_evidence.py#L76); [test_field_evidence.py::TestFieldSourcesPartialApply.test_new_address_plus_older_context_grants_no_authority](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_field_evidence.py#L101).

### C09 · P0 · Quoted, denied and hypothetical plans

**Type:** memory. **Scenario:** An email quotes Thursday dinner, then replaces it with Saturday. A DM says “Not Friday” and discusses a hypothetical alternative.

**Check:** Only the accepted replacement is current. Quotation, negation and a question do not reinstate an old plan.

**Regression evidence:** [test_regressions.py::TestApplyDatesEachFieldFromTheLinesTheReaderCited.test_a_line_quoting_the_old_arrangement_is_not_evidence_for_it](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3976); [test_regressions.py::TestApplyDatesEachFieldFromTheLinesTheReaderCited.test_a_denial_of_the_old_place_does_not_reinstate_it](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3991); [test_daytime_part5.py::TestAuthorshipBoundaries.test_questions_and_quotations_assert_no_fact](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part5.py#L465).

### C10 · P0 · Ambiguous cancellation must not hit the nearest event

**Type:** memory. **Scenario:** Two appointments share a day/provider; a text says “that appointment is cancelled” without enough identity. Later a source clarifies which one.

**Check:** Before clarification: do not assert either cancellation. After: cancel only the identified appointment and retain the other.

**Regression evidence:** [test_cancellation_linking.py::TestACancellationPlacesItselfOnTheSlotItNamed.test_a_matching_hour_does_not_authorize_a_cancellation](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_cancellation_linking.py#L52); [test_regressions.py::TestACancellationWithNoTargetIsKeptRatherThanInventedOrDropped.test_an_ambiguous_one_asks_instead_of_choosing](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3679); [test_regressions.py::TestACancellationWithNoTargetIsKeptRatherThanInventedOrDropped.test_another_appointment_at_the_same_provider_is_never_the_target](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3588).

### C11 · P0 · Replacement is not a second outing

**Type:** memory. **Scenario:** An email explicitly replaces Thursday dinner with Saturday; a second invitation later proposes another date and is declined.

**Check:** One current dinner; retain historical change if asked, but no extra Thursday/Friday commitment.

**Regression evidence:** [test_cancellation_linking.py::TestCancellationAndImmediateRebookingLifecycle.test_existing_booking_and_keyless_replacement_survive_merge_and_replay](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_cancellation_linking.py#L348); [test_calendar_booking_lifecycle.py::TestBookingSurvivesDuplicateArrival.test_cancelled_booking_and_replacement_both_survive](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_calendar_booking_lifecycle.py#L116).

### C12 · P0 · Older planning context can resolve a later invitation

**Type:** memory. **Scenario:** A group agreed a game date yesterday. A DM today asks someone else to join “our game.” Ingest the DM first, then the older group messages.

**Check:** Reconcile genuinely same-event evidence; older source may supply previously missing agreement without blindly undoing a newer direct correction.

**Regression evidence:** [test_plan_reconciliation.py::TestOlderPlanningEvidenceEnrichesAnUntouchedGuess.test_model_compares_both_sources_and_applies_older_plan_once](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_plan_reconciliation.py#L74); [test_plan_reconciliation.py::TestOlderPlanningEvidenceEnrichesAnUntouchedGuess.test_typed_correction_during_comparison_cannot_be_undone](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_plan_reconciliation.py#L154).

### C13 · P0 · Assistant error must not become personal memory

**Type:** memory. **Scenario:** Source says Friday. Assistant incorrectly answers Thursday. A fresh session asks again with no user confirmation of Thursday.

**Check:** Retain source-supported Friday. Assistant prose alone must not create or correct a commitment.

**Regression evidence:** [test_daytime_part6.py::TestCompanions.test_wrong_assistant_answer_leaves_no_trace](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part6.py#L270); [test_daytime_part5.py::TestOldSessionsGetToday.test_user_turn_archived_once_assistant_never](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part5.py#L187).

### C14 · P0 · Session survives midnight but context must refresh

**Type:** integration. **Scenario:** Keep an agent session open overnight; collect a correction in the morning, then ask the same question. Compare with a fresh session.

**Check:** Both paths see the new state. A previously acknowledged snapshot cannot suppress genuinely new context.

**Regression evidence:** [test_daytime_part5.py::TestOldSessionsGetToday.test_next_day_turn_supersedes_with_hints](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part5.py#L146); [test_daytime_part5.py::TestOldSessionsGetToday.test_resume_reemits_and_failure_warns](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part5.py#L174).

### C15 · P1 · Short replies, reactions and topic order

**Type:** memory. **Scenario:** Use an invitation followed by “yes,” an emoji or reaction. Put the relevant topic after a short reaction in a separate variant.

**Check:** Preserve short messages and correctly associate acceptance only where context establishes it. Do not drop low-word-count evidence.

**Regression evidence:** [test_gate_chat_streams.py::TestBareReactionsPassOnChat.test_bare_emoji_passes_on_every_chat_stream](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_gate_chat_streams.py#L42); [test_regressions.py::TestReportedMemoryFailures.test_a_reaction_is_rescued_when_the_topic_appears_after_it](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L254).

### C16 · P1 · Namesakes and identity changes

**Type:** memory. **Scenario:** Two distinct people share a first name. One changes a display name or gains a contact alias during the week.

**Check:** Do not merge the two people; retain the same person through a rename. A contact/UID change is not an RSVP.

**Regression evidence:** [test_core.py::TestMatching.test_two_alexes_do_not_collide_by_subject](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_core.py#L148); [test_review_fixes.py::TestStableConversationIdentity.test_equal_names_and_rosters_stay_separate_and_rename_keeps_identity](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_review_fixes.py#L325); [test_regressions.py::TestAPartifulEventStillOnTheCalendarIsNotDeclined.test_an_identity_change_is_not_an_rsvp](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L375).

### C17 · P1 · Owner, companion, host and visitor roles

**Type:** memory. **Scenario:** A performer appears on a ticket; Rowan is invited but conditional; Morgan visits the owner. Ask who accompanies the owner and who is hosting.

**Check:** Do not list performers as companions or rewrite a visitor as the owner. Preserve tentative status.

**Regression evidence:** [test_event_semantic_context.py::TestStructuredAttendance.test_performers_are_saved_as_hosts_and_not_companions](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L82); [test_event_semantic_context.py::TestReadableEventWording.test_visit_to_user_uses_visitor_subject_and_preserves_tentative_status](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L105).

### C18 · P1 · Recurring plans and one-instance exceptions

**Type:** memory. **Scenario:** Weekly games move once, then resume the ordinary cadence; later the recurring series ends.

**Check:** Moved instance keeps its identity and does not reset the cadence. Ended series produces no new appointments; historical instances remain.

**Regression evidence:** [test_regressions.py::TestOneWeekMovedIsNotTheCadenceMoving.test_the_excepted_day_is_not_materialised_again](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L2286); [test_regressions.py::TestAStandingAppointmentReachedTheCalendarAsOneDatedCopy.test_the_repeat_does_not_anchor_on_a_week_that_was_moved](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L2615); [test_ended_workshop_identity.py::TestEndedScheduleCannotContaminateNewAppointment.test_calendar_import_and_wiki_grouping_cannot_reuse_ended_workshop](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_ended_workshop_identity.py#L32).

### C19 · P1 · A plan elapsing does not prove attendance

**Type:** memory. **Scenario:** Arrange a workshop, let its date pass, and ask whether the owner attended. Later provide explicit attendance or decline evidence.

**Check:** First answer distinguishes agreed plan from observed attendance. Later source can establish the outcome.

**Regression evidence:** [test_elapsed_plans.py::TestElapsedPlans.test_elapsed_plans_do_not_prove_attendance](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_elapsed_plans.py#L20); [test_elapsed_plans.py::TestElapsedPlans.test_later_attendance_evidence_outranks_old_clock](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_elapsed_plans.py#L54).

### C20 · P1 · Visit spans and internal departures

**Type:** memory. **Scenario:** A guest stays Friday–Monday. They leave briefly on Saturday and return; another message explicitly changes the final departure.

**Check:** Internal trip does not shorten the stay. Only supported final-end change does. Ongoing visits remain discoverable after their start.

**Regression evidence:** [test_evidence_boundaries.py::TestExcursionDepartureIsNotTheStayEnd.test_departure_cannot_shorten_existing_weekend_before_merge_or_on_replay](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_evidence_boundaries.py#L113); [test_evidence_boundaries.py::TestExcursionDepartureIsNotTheStayEnd.test_a_cited_explicit_end_remains_actionable](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_evidence_boundaries.py#L127); [test_month_calendar.py::TestWholeCalendarBrowsing.test_next_thirty_days_includes_ongoing_visits_and_names](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_month_calendar.py#L19).

### C21 · P1 · Date language at boundaries

**Type:** memory. **Scenario:** Use “Thursday,” “next Thursday,” a stated past year, a month/day around New Year and a DST transition with explicit offsets.

**Check:** Resolve relative dates against when said, not query/ingestion day; preserve stated years and equivalent instants. Reject genuinely unsupported dates rather than guessing.

**Regression evidence:** [test_affinity.py::TestCodeDoesTheCalendarArithmetic.test_a_weekday_resolves_against_the_day_it_was_said](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_affinity.py#L296); [test_affinity.py::TestCodeDoesTheCalendarArithmetic.test_next_weekday_skips_a_week](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_affinity.py#L299); [test_affinity.py::TestAYearSomebodyWroteDownIsNotAGuess.test_a_stated_year_in_the_past_is_not_rolled_forward](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_affinity.py#L364); [test_clock_pin.py::TestPinnedClockTimezone.test_aware_pin_preserves_dst_fold](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_clock_pin.py#L49).

### C22 · P1 · Long history must not hide a small future plan

**Type:** memory. **Scenario:** Surround one upcoming visit and one pending invitation with many old plans and busy unrelated threads. Ask about the upcoming week.

**Check:** Relevant retained facts remain reachable; truncated context must not claim exhaustive coverage. Measure retrieval, not merely whether the brief lists every row.

**Regression evidence:** [test_brief_budget.py::TestUpcomingVisitSurvivesACrowdedMonth.test_recent_past_and_distant_plans_do_not_hide_an_upcoming_visit](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_brief_budget.py#L18); [test_prior_plan_context.py::TestPriorPlanDetailsAreRestored.test_upcoming_plan_is_not_crowded_out_by_many_past_topics](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_prior_plan_context.py#L78); [test_later_people.py::TestMonthBriefAndYearLookup.test_trimmed_month_does_not_claim_complete_event_coverage](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_later_people.py#L138).

### C23 · P1 · Unrelated thread cannot lend facts

**Type:** memory. **Scenario:** A newsletter and a group chat share a topic word; another DM mentions a vague date. Only the group has the real venue.

**Check:** Do not import unrelated facts or private context solely from weak word overlap. A related DM may retrieve the correct group evidence when identity/topic support it.

**Regression evidence:** [test_evidence_boundaries.py::TestRestoredContextCannotCreateUnrelatedPlans.test_newsletter_cannot_retrieve_group_chat_context](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_evidence_boundaries.py#L22); [test_prior_plan_context.py::TestPriorPlanDetailsAreRestored.test_same_topic_in_a_different_chat_is_not_retrieved](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_prior_plan_context.py#L43); [test_event_semantic_context.py::TestRelatedGroupContext.test_dm_restores_retained_group_topic_replies_roster_and_citable_ids](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L152).

### C24 · P1 · Questions and tasks belong to their speaker

**Type:** memory. **Scenario:** One person asks the owner a favor; the owner asks another person a different question in the same bundle. Later one is answered.

**Check:** Attribute requests correctly and close only the answered request, not a task sharing a word.

**Regression evidence:** [test_question_attribution.py::TestQuestionAttributionUsesCitedMessages.test_outgoing_evidence_cannot_be_attributed_to_the_recipient](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_question_attribution.py#L70); [test_live_write.py::TestAnswerResolvesWithoutClosingTheWrongTodo.test_an_already_answered_question_does_not_close_a_word_sharing_todo](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_live_write.py#L685).

### C25 · P1 · Open obligations wait for genuinely later evidence

**Type:** memory. **Scenario:** A person says they will send a document. Earlier chatter mentions the same document; a later message delivers it.

**Check:** Earlier traffic cannot satisfy the newly opened obligation; later supported delivery can. Do not treat word overlap alone as completion.

**Regression evidence:** [test_wakes.py::TestWakeEvidence.test_pre_open_traffic_cannot_support_a_wake](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wakes.py#L263); [test_wakes.py::TestCandidatePassNeverWritesWokeAt.test_word_overlap_nominates_but_leaves_the_waiter_asleep](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wakes.py#L90); [test_wakes.py::TestAcceptedWakeDecision.test_acceptance_with_a_valid_citation_wakes_without_closing](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wakes.py#L118).

### C26 · P1 · Stable preferences and direct corrections

**Type:** memory. **Scenario:** Store an address or preference, later correct it directly, then reingest old messages. Include a namesake with a different preference.

**Check:** Correction survives replay, history stays attributable, and namesake facts never move to the owner.

**Regression evidence:** [test_wiki_memory.py::TestACorrectionBeatsAStaleDreamFact.test_a_direct_correction_outlives_a_replayed_dream_fact](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wiki_memory.py#L270); [test_wiki_memory.py::TestWhoCountsAsTheUser.test_a_dream_fact_for_a_namesake_stays_off_the_self_page](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wiki_memory.py#L234).

### C27 · P1 · Instructions to an assistant are not personal commitments

**Type:** memory. **Scenario:** The owner asks an agent to draft a plan, then separately tells a friend they will actually attend.

**Check:** Do not turn delegated machine instructions into a confirmed social plan; do recognize the separate personal commitment.

**Regression evidence:** [test_bundle_shape.py::TestACommandToAMachineIsNotACommitmentHeMade.test_an_instruction_to_a_machine_is_not_his_own_commitment](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_bundle_shape.py#L58); [test_bundle_shape.py::TestACommandToAMachineIsNotACommitmentHeMade.test_the_same_words_to_a_person_are_still_his_own_commitment](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_bundle_shape.py#L64).

### C28 · P1 · Mail threading and quote provenance

**Type:** transport. **Scenario:** Continue a dinner thread by reply references, then send a new subject with a distinct event. Include quoted history and an outgoing reply.

**Check:** Keep the reply conversation intact and the new subject separate; preserve sender/owner and source bytes.

**Regression evidence:** [test_regressions.py::TestMailIsIncludedByDefaultAndRankedNotDropped.test_a_reply_joins_its_own_conversation_and_a_new_subject_does_not](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3437); [test_regressions.py::TestNormalisingMessageIdsDoesNotDuplicateArchivedMail.test_a_reply_joins_an_ancestor_archived_under_the_old_spelling](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3911).

### C29 · P1 · Duplicate delivery and replay

**Type:** transport. **Scenario:** Deliver the same message twice, replay a completed nightly batch and resend a correction with the same operation identity.

**Check:** No extra event, attendee or fabricated second correction. Compare counts/state before and after replay.

**Regression evidence:** [test_daytime_part3.py::TestNewActivityNominatesWithoutChangingFacts.test_duplicate_delivery_does_not_inflate_the_count](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part3.py#L85); [test_daytime_part3.py::TestSourceBackedPrecedence.test_replay_of_a_cited_correction_is_a_no_op](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part3.py#L601); [test_live_write.py::TestReplayingAnOperationCannotUndoALaterOne.test_a_replayed_move_does_not_restore_the_date_a_later_move_replaced](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_live_write.py#L576).

### C30 · P1 · Partial ingestion and concurrent arrivals

**Type:** integration. **Scenario:** A source fails midway; another healthy source finishes. New messages arrive while the nightly pass is running.

**Check:** Do not mark missing/unread material complete. Preserve new arrivals for the next pass and report incomplete collection separately from recall failure.

**Regression evidence:** [test_daytime_part2.py::TestDownSourcesDoNotSuppressHealthyOnes.test_unavailable_is_recorded_and_the_healthy_source_finishes](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part2.py#L263); [test_daytime_part3.py::TestReviewClearsOnlyWhatItCovered.test_arrivals_during_a_pass_survive_it](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part3.py#L180); [test_web.py::TestTheProposeBarWasAnAnimationRatherThanAFraction.test_a_failed_request_does_not_count_as_read](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_web.py#L850).

### C31 · P2 · Calendar identity changes without social change

**Type:** transport. **Scenario:** Move the same booking between calendars or change its UID, then independently cancel a different booking.

**Check:** One retained booking; no inferred RSVP change from identity maintenance. Only actual cancellation evidence changes attendance.

**Regression evidence:** [test_regressions.py::TestACalendarRenameIsNotAHundredNewEvents.test_the_same_event_under_a_new_calendar_uid_stays_one_row](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L308); [test_calendar_memory_boundaries.py::TestDeletingOneCalendarCopyDoesNotCancelTheOccasion.test_new_uid_in_another_calendar_preserves_the_same_weekend_and_replay](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_calendar_memory_boundaries.py#L31).

### C32 · P2 · Muted or blocked source boundaries

**Type:** integration. **Scenario:** A muted group and an explicitly blocked email sender contain tempting relevant details; an ordinary low-priority newsletter later contains a real appointment update.

**Check:** Honor declared source policy; automatic low relevance must not mean permanent exclusion. Report differing supported source-policy capabilities instead of forcing a common hidden policy.

**Regression evidence:** [test_event_semantic_context.py::TestRelatedGroupContext.test_muted_group_is_not_restored](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L171); [test_regressions.py::TestMailIsIncludedByDefaultAndRankedNotDropped.test_a_blocked_senders_body_is_never_opened](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3397); [test_regressions.py::TestMailIsIncludedByDefaultAndRankedNotDropped.test_a_bulk_senders_body_is_fetched_and_kept](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L3368).

### C33 · P2 · Abbreviations need evidence, not confident expansion

**Type:** memory. **Scenario:** A message names “MR Hall.” Later a separate source spells out the same venue; another abbreviation has no expansion anywhere.

**Check:** Expand only the supported alias; preserve uncertainty on the other. Do not invent a full venue name.

**Regression evidence:** [test_event_semantic_context.py::TestReadableEventWording.test_verified_full_wording_expands_stored_title_and_location](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L119); [test_event_semantic_context.py::TestReadableEventWording.test_an_invented_expansion_stays_explicitly_unresolved](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py#L127).

### C34 · P2 · Uncited neighboring details cannot leak into a record

**Type:** memory. **Scenario:** A delivery notice and meeting URL sit near one another but concern separate events. Later a source explicitly links the right URL.

**Check:** No meeting link copied from proximity alone; linked evidence may add it later. Score source-backed fields, not accidental retrieval neighborhood.

**Regression evidence:** [test_evidence_boundaries.py::TestRestoredContextCannotCreateUnrelatedPlans.test_cited_delivery_cannot_inherit_uncited_meeting_url](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_evidence_boundaries.py#L67); [test_evidence_boundaries.py::TestRestoredContextCannotCreateUnrelatedPlans.test_merged_citations_can_still_supply_the_actual_meeting_link](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_evidence_boundaries.py#L82).

### C35 · P2 · Calendar placeholders and invitations are not acceptance

**Type:** memory. **Scenario:** An event invitation hides its location until RSVP; another source announces a real venue without confirming owner attendance.

**Check:** Do not treat withheld location or a placeholder as decline/acceptance. Distinguish opportunity, invitation and commitment from actual source statements.

**Regression evidence:** [test_regressions.py::TestAWithheldLocationWasReadAsAnRSVP.test_the_placeholder_is_not_an_rsvp](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L2783); [test_regressions.py::TestAWithheldLocationWasReadAsAnRSVP.test_a_withheld_location_is_never_a_decline](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L2818); [test_regressions.py::TestAnInvitationWasJustAnotherMaybe.test_an_unanswered_invitation_is_not_could_go](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py#L1356).

## What belongs in qualification, not the memory score

Installation/update rollback, CLI cleanup, credential setup, web CSRF checks, packaging, publishing, progress bars and filesystem locks are important product tests, but not evidence that a memory provider remembered a fact. Keep them in operational validation. Source pagination, duplicate delivery, clock fidelity and context refresh can invalidate the comparison, so qualify them separately rather than turning a broken adapter into a low semantic grade.

A few implementation-specific tests should **not** become universal requirements: MemCal's named handles, wiki layout, gate settings, typed write schema and nightly architecture are product choices. The portable challenge is the observable behavior, not matching those internal objects or forcing other providers to use the same implementation.

## Generation and human-review checklist

1. Verify the requested silence really contains zero messages from that participant until the delayed reply. Check hidden off-thread acceptance too.
2. Inspect every source-established commitment, including authored chatter. An unscored side event can still change the correct answer to a broad question.
3. Verify cross-thread causal order: a character cannot react to an update they have not received; relaying it must not look like independently agreeing the same change twice.
4. Check changes preserve unspecified fields, declined alternatives stay declined, and old quoted text is visibly old.
5. Review naturalness and uneven participation. Exact supplied messages are useful for one-line/short exchanges; use the writer where a richer conversation helps, not simply to inflate message counts.
6. Check claim evidence and question scope without seeing provider answers. Include correct/wrong/partial controls for the judge and preserve the denominator.
7. Review message/token volume, evidence age, repetition and position. A week of dense final-day recaps is not a week-long retention test.
8. Preserve rejected generation and reviewer uncertainty; do not silently reroll until a favorable verdict appears.

## Complete module coverage ledger

Counts below are statically discovered `test*` methods in top-level classes, not collected/executed-test totals (inheritance, parameterization and wrappers can change runtime counts). Modules can contain both narrative and operational lessons. Every listed module was included in the inventory; only tests relevant to the catalog are linked above.

| Module | Test definitions | Triage |
|---|---:|---|
| [test_affinity.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_affinity.py) | 46 | Memory/state/context candidates |
| [test_attendee_updates.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_attendee_updates.py) | 7 | Memory/state/context candidates |
| [test_bridge.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_bridge.py) | 13 | Operational/product coverage; not a memory-score task |
| [test_brief_budget.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_brief_budget.py) | 5 | Memory/state/context candidates |
| [test_bundle_shape.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_bundle_shape.py) | 12 | Memory/state/context candidates |
| [test_calendar_booking_lifecycle.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_calendar_booking_lifecycle.py) | 11 | Memory/state/context candidates |
| [test_calendar_bridge.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_calendar_bridge.py) | 12 | Operational/product coverage; not a memory-score task |
| [test_calendar_memory_boundaries.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_calendar_memory_boundaries.py) | 6 | Memory/state/context candidates |
| [test_calendar_permission_status.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_calendar_permission_status.py) | 7 | Operational/product coverage; not a memory-score task |
| [test_cancellation_linking.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_cancellation_linking.py) | 33 | Memory/state/context candidates |
| [test_cli_lifecycle.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_cli_lifecycle.py) | 6 | Operational/product coverage; not a memory-score task |
| [test_clock_pin.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_clock_pin.py) | 4 | Lifecycle/readiness qualification |
| [test_codex_model_setup.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_codex_model_setup.py) | 11 | Operational/product coverage; not a memory-score task |
| [test_collection_jobs.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_collection_jobs.py) | 49 | Source fidelity/policy qualification |
| [test_config_secret.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_config_secret.py) | 9 | Operational/product coverage; not a memory-score task |
| [test_core.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_core.py) | 587 | Memory/state/context candidates |
| [test_daytime_freshness_fixes.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_freshness_fixes.py) | 31 | Memory/state/context candidates |
| [test_daytime_part2.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part2.py) | 14 | Lifecycle/readiness qualification |
| [test_daytime_part3.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part3.py) | 32 | Memory/state/context candidates |
| [test_daytime_part4.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part4.py) | 20 | Memory/state/context candidates |
| [test_daytime_part5.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part5.py) | 14 | Memory/state/context candidates |
| [test_daytime_part6.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_part6.py) | 12 | Memory/state/context candidates |
| [test_daytime_review_completion.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_review_completion.py) | 2 | Lifecycle/readiness qualification |
| [test_daytime_stage1.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_daytime_stage1.py) | 26 | Lifecycle/readiness qualification |
| [test_doctor_access.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_doctor_access.py) | 9 | Operational/product coverage; not a memory-score task |
| [test_dream_diagnostics.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_dream_diagnostics.py) | 9 | Operational/product coverage; not a memory-score task |
| [test_dream_live.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_dream_live.py) | 12 | Lifecycle/readiness qualification |
| [test_dream_naming.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_dream_naming.py) | 12 | Memory/state/context candidates |
| [test_dream_output.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_dream_output.py) | 11 | Operational/product coverage; not a memory-score task |
| [test_dream_usability.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_dream_usability.py) | 13 | Memory/state/context candidates |
| [test_elapsed_plans.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_elapsed_plans.py) | 8 | Memory/state/context candidates |
| [test_email_source_controls.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_email_source_controls.py) | 6 | Source fidelity/policy qualification |
| [test_ended_workshop_identity.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_ended_workshop_identity.py) | 8 | Memory/state/context candidates |
| [test_event_semantic_context.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_event_semantic_context.py) | 18 | Memory/state/context candidates |
| [test_evidence_boundaries.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_evidence_boundaries.py) | 10 | Memory/state/context candidates |
| [test_field_evidence.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_field_evidence.py) | 19 | Memory/state/context candidates |
| [test_gate_chat_streams.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_gate_chat_streams.py) | 8 | Source fidelity/policy qualification |
| [test_groupme.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_groupme.py) | 17 | Source fidelity/policy qualification |
| [test_hermes.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_hermes.py) | 34 | Lifecycle/readiness qualification |
| [test_hermes_delivery.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_hermes_delivery.py) | 1 | Lifecycle/readiness qualification |
| [test_ical_siri_dedup.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_ical_siri_dedup.py) | 5 | Source fidelity/policy qualification |
| [test_integration_packaging.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_integration_packaging.py) | 7 | Operational/product coverage; not a memory-score task |
| [test_later_people.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_later_people.py) | 11 | Memory/state/context candidates |
| [test_live_series_source.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_live_series_source.py) | 11 | Memory/state/context candidates |
| [test_live_write.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_live_write.py) | 50 | Memory/state/context candidates |
| [test_llm_providers.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_llm_providers.py) | 45 | Operational/product coverage; not a memory-score task |
| [test_local_model_contract.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_local_model_contract.py) | 4 | Operational/product coverage; not a memory-score task |
| [test_merge.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_merge.py) | 36 | Memory/state/context candidates |
| [test_month_calendar.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_month_calendar.py) | 2 | Memory/state/context candidates |
| [test_nightly_evidence_order.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_nightly_evidence_order.py) | 2 | Memory/state/context candidates |
| [test_open_includes_activity.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_open_includes_activity.py) | 3 | Memory/state/context candidates |
| [test_openai_compatible.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_openai_compatible.py) | 5 | Operational/product coverage; not a memory-score task |
| [test_openclaw.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_openclaw.py) | 8 | Lifecycle/readiness qualification |
| [test_operational_regressions.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_operational_regressions.py) | 311 | Operational/product coverage; not a memory-score task |
| [test_permissions.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_permissions.py) | 12 | Operational/product coverage; not a memory-score task |
| [test_plan_reconciliation.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_plan_reconciliation.py) | 17 | Memory/state/context candidates |
| [test_platform.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_platform.py) | 52 | Operational/product coverage; not a memory-score task |
| [test_polled.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_polled.py) | 28 | Source fidelity/policy qualification |
| [test_prefix_waves.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_prefix_waves.py) | 3 | Lifecycle/readiness qualification |
| [test_prior_plan_context.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_prior_plan_context.py) | 7 | Memory/state/context candidates |
| [test_propose_breaker.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_propose_breaker.py) | 10 | Lifecycle/readiness qualification |
| [test_quality.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_quality.py) | 12 | Operational/product coverage; not a memory-score task |
| [test_question_attribution.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_question_attribution.py) | 6 | Memory/state/context candidates |
| [test_regressions.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_regressions.py) | 282 | Memory/state/context candidates |
| [test_relevant_events.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_relevant_events.py) | 8 | Memory/state/context candidates |
| [test_reminder_delivery.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_reminder_delivery.py) | 8 | Lifecycle/readiness qualification |
| [test_resume.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_resume.py) | 18 | Lifecycle/readiness qualification |
| [test_review_baselines.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_review_baselines.py) | 11 | Memory/state/context candidates |
| [test_review_fixes.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_review_fixes.py) | 20 | Memory/state/context candidates |
| [test_runtime_profile_repairs.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_runtime_profile_repairs.py) | 16 | Lifecycle/readiness qualification |
| [test_schedule_catchup.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_schedule_catchup.py) | 5 | Lifecycle/readiness qualification |
| [test_schedule_toggle.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_schedule_toggle.py) | 9 | Operational/product coverage; not a memory-score task |
| [test_separate_occasions.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_separate_occasions.py) | 4 | Memory/state/context candidates |
| [test_settings.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_settings.py) | 56 | Operational/product coverage; not a memory-score task |
| [test_setup_config.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_setup_config.py) | 14 | Operational/product coverage; not a memory-score task |
| [test_signal.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_signal.py) | 20 | Source fidelity/policy qualification |
| [test_slack.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_slack.py) | 32 | Source fidelity/policy qualification |
| [test_source_preview.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_source_preview.py) | 4 | Memory/state/context candidates |
| [test_spam_boundaries.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_spam_boundaries.py) | 3 | Source fidelity/policy qualification |
| [test_stages.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_stages.py) | 35 | Lifecycle/readiness qualification |
| [test_telegram.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_telegram.py) | 20 | Source fidelity/policy qualification |
| [test_ui_contracts.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_ui_contracts.py) | 4 | Operational/product coverage; not a memory-score task |
| [test_update.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_update.py) | 20 | Operational/product coverage; not a memory-score task |
| [test_update_components.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_update_components.py) | 5 | Operational/product coverage; not a memory-score task |
| [test_update_safety.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_update_safety.py) | 9 | Operational/product coverage; not a memory-score task |
| [test_wakes.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wakes.py) | 14 | Memory/state/context candidates |
| [test_web.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_web.py) | 139 | Operational/product coverage; not a memory-score task |
| [test_web_security.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_web_security.py) | 14 | Operational/product coverage; not a memory-score task |
| [test_whois.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_whois.py) | 46 | Memory/state/context candidates |
| [test_wiki_citations.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wiki_citations.py) | 12 | Memory/state/context candidates |
| [test_wiki_memory.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wiki_memory.py) | 46 | Memory/state/context candidates |
| [test_wiki_persistence.py](https://github.com/cuuush/memcal/blob/dea42cfdcb4f0dbf3375054dfd96e0f8a408d55a/tests/test_wiki_persistence.py) | 13 | Lifecycle/readiness qualification |

## Limits and next use

This catalog does not add all challenges to Pizza Week automatically. Select a small coherent set, keep source-only review and human review, and retain separate simple controls. Some ideas require transport capabilities that a provider or host may not support; label those conditions explicitly rather than silently manufacturing native support. No paid inference, provider runs or performance comparisons were performed to create this document.
