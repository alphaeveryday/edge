"""What the agent reads about each graph tool, in one order for every tool.

The order is the Arcade "Tool Description" and "Dependency Hint" patterns: a summary line, what comes back, the
tool to call first, the tool to call next, and the tool to use instead. Argument text goes on the argument.
Descriptions used to be one paragraph each, written at different times; the same fact (where an id comes from,
what to call next) sat in a different place in each, or was missing.
"""

RESULT = 'An earlier result, as {"tool_run_id":"..."}. Copy the tool_run_id of that result.'
LIMIT = 'Rows to show at most. Rows that fit one response are shown whole whatever this is; leave it out.'

TOOLS = {
    'get_ontology_schema': {
        'summary': 'List the object types and links this data has, or the properties of the types you name.',
        'returns': 'Type and link names. With object_types: their properties. These are definitions, not observed facts.',
        'alternative': 'The search, ticker and holdings tools already name the types they take. Do not call this as a precaution before them.',
        'args': {'object_types': 'Types whose properties you want. Leave out for the list of every type and link.'}},
    'search_objects': {
        'summary': 'Find objects of one type by part of their name, title or ticker.',
        'returns': 'A table of every match with its object_id. data_scope says how many objects of the type exist and which properties were matched. '
                   'When nothing matches, it says so and shows what each word alone matches: do not repeat the search with other spellings.',
        'follow_up': 'Pass object_type and object_id to the tool that reads the object (get_etf_holdings, get_linked_objects, get_objects).',
        'alternative': 'find_securities_by_ticker when you have exchange tickers.',
        'args': {'object_type': 'A security is Equity or ETF. An issuer is Company.',
                 'query': 'Part of the name, title or ticker. Leave out to list every object of the type.',
                 'filters': 'Exact property values, as {"property":"value"}. Property names come from get_ontology_schema.', 'limit': LIMIT}},
    'get_objects': {
        'summary': 'Read objects by their typed ids, many in one call.',
        'returns': 'A table with one row per id and its status. An id that is not in the data stays in the table as not found.',
        'prerequisite': 'An object_id from search_objects, find_securities_by_ticker or another result.',
        'args': {'object_refs': 'Objects to read, as [{"object_type":"...","object_id":"..."}].'}},
    'get_linked_objects': {
        'summary': 'Follow one declared link from objects to everything linked to them, for example from a company to the securities it issued.',
        'returns': 'A table of the linked objects, each with source_id: the object it was reached from.',
        'prerequisite': 'An object_id from a search. Link names read Source_Relation_Target; get_ontology_schema lists them.',
        'alternative': 'Use this instead of searching by name variants when the question is about a relation between two things.',
        'args': {'object_refs': 'Start objects. They may be either end of the link.',
                 'link_type': 'The link to follow.',
                 'direction': 'Needed only for a link between two objects of one type. Otherwise the type of object_refs decides.', 'limit': LIMIT}},
    'find_securities_by_ticker': {
        'summary': 'Turn exchange tickers into securities, many in one call.',
        'returns': 'One row per ticker with its status and object id. A ticker that is not in the data stays as not found.',
        'follow_up': 'Use the returned ids, or pass {"kind":"selection_ref","ref":{"tool_run_id":...}} as targets to price, flow and event tools.',
        'alternative': 'search_objects when you have a name and no ticker. An object another tool already returned needs no lookup here.',
        'args': {'tickers': 'Tickers given in the question. Do not use remembered tickers.', 'market_code': 'XKRX for Korean stocks and funds.',
                 'security_type': 'Equity, ETF, or both.'}},
    'get_result_page': {
        'summary': 'Read the next rows of an earlier result that did not fit one response.',
        'returns': 'The rows from offset on, with page.next_offset when more remain. No new query runs and the cutoff is unchanged.',
        'prerequisite': 'A result whose page.complete is false. Its notice gives the offset to continue from.',
        'args': {'dataset_ref': RESULT, 'offset': 'The page.next_offset of the earlier result.', 'limit': LIMIT}},

    'get_etf_holdings': {
        'summary': 'Read every direct holding of one fund on one date, with its stored weight.',
        'returns': 'A table of holdings with weight_ratio (0.05 means 5%) and status, and selected_date: the snapshot date actually used. '
                   'Weights are as stored: not rescaled to 100%, and the snapshot is not certified complete.',
        'prerequisite': 'The fund id from search_objects or find_securities_by_ticker.',
        'follow_up': 'sum_holding_weights for any total of weights. compare_holdings_between_dates for the same fund on two dates.',
        'args': {'etf_ref': 'The fund, as {"object_type":"ETF","object_id":"..."}.', 'holdings_date': 'YYYY-MM-DD, a Korean market day.',
                 'date_policy': 'exact fails when that day has no snapshot. latest_on_or_before takes the nearest earlier snapshot; state selected_date in the answer.',
                 'limit': LIMIT}},
    'sum_holding_weights': {
        'summary': 'Add stored holding weights of one fund: all holdings, a named subset, or the largest N.',
        'returns': 'One row with the raw weight sum, how many holdings it covers and how many have no weight.',
        'prerequisite': 'A get_etf_holdings result.',
        'alternative': 'Use this for every weight total, concentration or combined exposure in the answer instead of adding weights yourself.',
        'args': {'selection_ref': RESULT, 'members': 'Holdings to add, as object refs. Leave out with top_n for all holdings.',
                 'top_n': 'Add the N largest holdings. Not together with members.'}},
    'compare_holdings_between_dates': {
        'summary': 'Compare the holdings of one fund on an earlier and a later date.',
        'returns': 'One row per holding: present on each date, and the change of its stored weight. A weight change is not evidence of a trade.',
        'prerequisite': 'Two get_etf_holdings results of the same fund.',
        'args': {'earlier_ref': RESULT, 'later_ref': RESULT}},
}


def text(name):
    """The description of one tool, in the standard order."""
    entry = TOOLS[name]
    lines = [entry['summary']] + [label + ': ' + entry[key] for key, label in
        (('returns', 'Returns'), ('prerequisite', 'Prerequisite'), ('follow_up', 'Follow-up'), ('alternative', 'Alternative')) if entry.get(key)]
    return '\n'.join(lines)
