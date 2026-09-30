# Pizza Day authoring

The author writes one scene and a short list of plain-language facts. The facts are targets for the writer and verifier, not automatic truth. The generated dialogue must support each fact with cited messages before PACBench freezes the evidence and asks either provider a question.

[Pizza Day](../examples/pizza_day.json) uses five independent facts: companion, restaurant, street, date, and time. The date fact asks for Friday, September 11. It does not require the year in an assistant's answer. This phrasing gives the answer judge one concrete decision per detail without a fixed slot schema.

The writer can choose the number of messages and add ordinary side chatter. PACBench assigns timestamps after generation. The verifier checks the scene and every fact against the dialogue. The contract keeps supported facts and citations, reviews the question for answer leaks or false premises, and freezes both before provider runs. The scoring judge receives the question, answer, and frozen claims; it does not receive the private scene.

A `scene` plus `test_intent` form remains supported by the generic runner. It is useful when the author wants the model to propose which dialogue-supported claims matter. Pizza Day uses explicit facts because the author knows exactly which details to test.

For a changing week, [Pizza Day week](../examples/pizza_day_week.json) places short facts beside each day's chat. The author writes Monday through Sunday, a chat name, a human time, and a scene. PACBench derives IDs and timestamps. Each evening it reviews the facts against all messages exposed so far: an unchanged plan carries forward, a correction replaces only its affected detail, and a new plan in the same chat coexists with the old one. The next morning's question uses that day's frozen contract. Quiet days still get checked without another ingestion.

The nine proposed Pizza Day calibration labels still need human review. A single bounded provider run is diagnostic; it cannot establish a memory-system winner.
