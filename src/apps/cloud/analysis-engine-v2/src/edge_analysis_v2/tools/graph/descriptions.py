"""What the agent reads about each graph tool, in one order for every tool.

The order is the Arcade "Tool Description" and "Dependency Hint" patterns: a summary line, what comes back, the
tool to call first, the tool to call next, and the tool to use instead. Argument text goes on the argument.
Descriptions used to be one paragraph each, written at different times; the same fact (where an id comes from,
what to call next) sat in a different place in each, or was missing.
"""

TARGETS = ('The securities or companies to look at. One of: {"kind":"objects","object_refs":[{"object_type":"Equity","object_id":"..."}]}, '
           '{"kind":"etf_holdings","etf_ref":{...},"holdings_date":"YYYY-MM-DD","date_policy":"latest_on_or_before"} for every constituent of a fund, '
           'or {"kind":"selection_ref","ref":{"tool_run_id":"..."}} to reuse the targets of an earlier result.')
START = 'First day, YYYY-MM-DD.'
END = 'Last day, YYYY-MM-DD. Not after the analysis cutoff.'
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
        'follow_up': 'Pass object_type and object_id to the tool that reads the object (get_etf_holdings, get_linked_objects, list_events).',
        'alternative': 'find_securities_by_ticker when you have exchange tickers. find_similar_events to find an event by words.',
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
    'list_events': {
        'summary': 'List everything that was reported about companies in a period, one line per event: contracts, launches, alliances, capacity changes, lawsuits and other events.',
        'returns': 'Every event of the period, none left out: company, reported_date, event_type, what (one word of the report), stages, reports and event_id. '
                   'When the events are too many for one response, none is listed: the rows count events per company and type, and you call again for fewer companies, one type or a shorter period. It is a table of contents: amounts, counterparts and details are not here. reported_date is the date of the report, not of a signing. '
                   'data_scope.coverage lists the stored event types that have no event here, so "not found in this data" is answered from this result.',
        'prerequisite': 'Company or security ids from a search, or a get_etf_holdings result.',
        'follow_up': 'get_event_timeline with the event_id of each event you will write about. read_article with event_id for the full text of its report.',
        'alternative': 'find_similar_events to look for earlier events of the same kind at any company.',
        'args': {'targets': TARGETS + ' A security is mapped to its issuing company.', 'start_date': START, 'end_date': END,
                 'event_types': 'Only these stored type codes, such as COMPANY.CONTRACT.SIGNING. Leave out to list every type.'}},
    'find_similar_events': {
        'summary': 'Find earlier events of one kind, at any company and over the whole data: precedents for an event you are judging.',
        'returns': 'One row per event with its content: event_type, reported_date, companies and roles, amounts as written (value_text), details in arguments, stages, '
                   'reports and event_ref. The rows are a sample, not every such event: total_rows says how many matched and page.complete whether all are shown. '
                   'When more than 100 match, no rows come back: overview lists counts by type and month, and you narrow. '
                   'An amount whose periodBasis is "not stated" can be a total over several contracts or years.',
        'prerequisite': 'An event type code from a list_events row, or words that name the kind of event. Give event_types, query, or both.',
        'follow_up': 'get_event_timeline with event_ref for how an earlier event went on.',
        'alternative': 'list_events to see everything that happened to known companies in a period.',
        'args': {'event_types': 'Stored type codes such as COMPANY.CONTRACT.SIGNING; a code that is not stored is an error.',
                 'query': 'Words that must all appear in the event (companies, contract object, counterpart) or in a report title.',
                 'targets': TARGETS + ' Leave out to look at every company.',
                 'start_date': START + ' Leave out to search from the start of the data.', 'end_date': END + ' Leave out to search up to the cutoff.',
                 'time_field': 'reported_event_date (default) filters by the date the report gave. available filters by when the report entered this data.',
                 'participant_conditions': 'Keep events where a company has a role, as [{"role_codes":["SUPPLIER"]}]. Each row then says matched, not_matched or unknown.',
                 'feature_filters': 'Keep events by a reported number, as [{"metric_code":"CONTRACT_VALUE","operator":"gte","value":"100000000000","unit":"KRW","period_basis":"TOTAL"}]. '
                                    'A report without that number is unknown, not a non-match.',
                 'available_since': 'Only reports that entered this data after this time, with a timezone. Finds new corrections of old events.',
                 'order_by': 'reported_date (default) lists events in time order. amount lists the largest won amount first: use it to say which event is the largest.', 'limit': LIMIT}},
    'get_event_timeline': {
        'summary': 'Read how one event went from announcement to signing, correction or cancellation: only the reports that changed what is known, in time order.',
        'returns': 'One row per report that added something: added (FIRST_IN_THREAD, FOLLOW_UP_STAGE or CORRECTION), reported_date, stage, companies and roles, '
                   'amounts, arguments, the document, and repeated_by: how many later reports only repeated it. One thread is one event. '
                   'An event without a stored thread returns one row that counts the reports of the same type, companies and reported date.',
        'prerequisite': 'event_id from a list_events row or event_ref from a find_similar_events row.',
        'follow_up': 'read_article with a document_ref for the full text of a report. read_document_excerpts for the opening lines of several.',
        'args': {'event_refs': 'Events to open, as [{"object_type":"SourceEvent","object_id":"..."}].', 'limit': LIMIT}},
    'read_article': {
        'summary': 'Read the full text of one news article or filing, from its stored link.',
        'returns': 'title, publisher, published_at and text: the page as it is on the web now, with menus and other headlines around the report. '
                   'A long page comes in parts: next_offset is where the next part starts. other_documents_of_this_event counts the other reports of the event.',
        'prerequisite': 'The event_id of a list_events row, or a document_ref of a get_event_timeline row. Give one of them.',
        'alternative': 'read_document_excerpts for the stored title and opening lines of many documents in one call.',
        'args': {'event_id': 'The event whose report to read, as the event_id of a list_events row. Its first stored document is read.',
                 'document_ref': 'The document to read, as {"object_type":"NewsArticle","object_id":"..."} from a timeline row.',
                 'offset': 'Character to start from. Leave out to start at the beginning; pass next_offset to continue.'}},
    'read_document_excerpts': {
        'summary': 'Read the title and the stored opening excerpt of news articles and filings.',
        'returns': 'One row per document with its text. The full article is not in this data. excerpt_is_cut_off marks an excerpt that stops mid-sentence: '
                   'do not complete a number, unit or name that is cut. Filings have a title and no text.',
        'prerequisite': 'document_ref from a get_event_timeline or find_similar_events row.',
        'args': {'document_refs': 'Documents to read, as [{"object_type":"NewsArticle","object_id":"..."}].',
                 'offset': 'Character to start from. Leave out to start at the beginning.', 'length': 'Characters to read at most.'}},
}


def text(name):
    """The description of one tool, in the standard order."""
    entry = TOOLS[name]
    lines = [entry['summary']] + [label + ': ' + entry[key] for key, label in
        (('returns', 'Returns'), ('prerequisite', 'Prerequisite'), ('follow_up', 'Follow-up'), ('alternative', 'Alternative')) if entry.get(key)]
    return '\n'.join(lines)
