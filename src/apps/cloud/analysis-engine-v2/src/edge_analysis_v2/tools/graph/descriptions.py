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
                 'query': 'Words that must all appear in the event (companies, contract object, counterpart) or in a report title. Without targets the longest word '
                          'must be in the event itself: an event that has it only in a report title is not found.',
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
                   'An event without a stored thread returns one row that counts the reports of the same type, companies and reported date. '
                   'Where added is "not recorded", the data does not say what each report added: every report is then a row and repeats are not folded.',
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
    'get_price_observations': {
        'summary': 'Read daily closing prices and volume of securities, and the NAV of funds when asked.',
        'returns': 'A table with one row per security and trading day; with include_nav a fund has a second row for the day that holds its NAV. '
                   'data_scope.dates_returned lists the days found and latest_date_available_at_cutoff the last day published; '
                   'with NAV, nav_dates_returned and latest_nav_date_available_at_cutoff say the same for the NAV rows. '
                   'A later day is not published yet, which is different from a day without trading. There is no open, high, low or adjusted close, '
                   'and whether a close is adjusted is not recorded: write "observed close".',
        'prerequisite': 'Security ids from a search, or a get_etf_holdings result as targets.',
        'follow_up': 'calculate_returns for a change between two days. count_rising_and_falling for how many holdings rose. calculate_nav_premium for price against NAV.',
        'args': {'targets': TARGETS, 'start_date': START, 'end_date': END, 'include_nav': 'true to add the fund NAV rows.'}},
    'calculate_returns': {
        'summary': 'Calculate the price change of each security between two exact trading days.',
        'returns': 'One row per security with start_price, end_price and return_ratio (0.05 means +5%). A day without a price gives status unavailable and names the days that have one. '
                   'It is the change between two recorded closes, not a total return.',
        'prerequisite': 'A get_price_observations result that contains both days.',
        'args': {'dataset_ref': RESULT, 'start_date': 'The earlier trading day.', 'end_date': 'The later trading day.',
                 'price_field': 'closePrice for the market price. nav for the fund NAV.',
                 'basis_policy': 'Leave out. require_recorded_basis answers only when the price basis is recorded, which it is not in this data.'}},
    'count_rising_and_falling': {
        'summary': 'Count how many of the securities rose, fell, stayed flat or have no price between two days, with the fund weight of each group.',
        'returns': 'One row per group with the number of securities and their summed stored weight. It is breadth, not performance attribution.',
        'prerequisite': 'A get_price_observations result with start_date and end_date, or a calculate_returns result alone.',
        'args': {'dataset_ref': RESULT, 'start_date': 'The earlier trading day. Only with a price result.', 'end_date': 'The later trading day. Only with a price result.'}},
    'calculate_nav_premium': {
        'summary': 'Compare the closing price of a fund with its NAV on one day.',
        'returns': 'One row per fund with close_price, nav and premium_ratio (negative means the price is below NAV).',
        'prerequisite': 'A get_price_observations result of the fund with include_nav true.',
        'args': {'dataset_ref': RESULT, 'trade_date': 'The trading day, YYYY-MM-DD.'}},
    'get_flow_observations': {
        'summary': 'Read net buying and selling of securities: per day by individuals, foreigners and institutions, or as intraday estimates for foreigners and institutions.',
        'returns': 'A table per security and day. Daily rows have net amounts as written in won and net share quantities. data_scope.net_value_runs says which securities each investor '
                   'has been net buying or selling and since which day: say "turned" only for the securities listed under that day. '
                   'Intraday rows are estimated net share quantities, not won amounts, and have no figure for individuals. Whether they are cumulative or per interval '
                   'is not known: do not sum them or compare them with daily flows.',
        'prerequisite': 'Security ids from a search, or a get_etf_holdings result as targets.',
        'follow_up': 'sum_investor_flows for a total over the days. Flows of holdings are not the flows of the fund itself.',
        'args': {'targets': TARGETS, 'start_date': START, 'end_date': END, 'frequency': 'daily for finalized flows. intraday for estimates during the day.'}},
    'estimate_contract_profit': {
        'summary': 'Estimate how much one contract adds to a company\'s yearly revenue and operating profit, as a range, against its last full year.',
        'returns': 'contract_stage (signed or not, read it first), estimate (per year: revenue, operating profit, each as a share of last year, and weighted by the fund), '
                   'confirmed_inputs (stored), assumptions (which are yours, a holding_weight you pass among them), and most_sensitive_assumption. '
                   'fund_weight_not_found says why no fund-weighted figure is given, and company_not_confirmed that the event stores no participants. '
                   'Every figure is per year of recognition, not per month. '
                   'Say it as an estimate with its assumptions.',
        'prerequisite': 'The event_id of a list_events row or event_ref of a find_similar_events row of a contract, and the company that earns it.',
        'alternative': 'Use this whenever the answer says how much an order matters for earnings or for a fund, instead of writing "large" or "limited".',
        'args': {'event_ref': 'The event, as {"object_type":"SourceEvent","object_id":"..."}. The amount and period are read from every report of that event.',
                 'company_ref': 'The company that earns the contract: a participant of the event, not its customer. Its security is accepted and mapped to the issuer.',
                 'years_low': 'Shortest recognition period in years, at least 0.01. Most reports store no period, so give your assumption with years_high.',
                 'years_high': 'Longest recognition period in years, at most 100.',
                 'margin_low': 'Lowest operating margin to test, 0.08 means 8%, between -1 and 1. Default: the company\'s last full-year margin.',
                 'margin_high': 'Highest operating margin to test.',
                 'company_share': 'This company\'s part of a shared contract, 0.5 means half. Default: the whole contract.',
                 'etf_ref': 'A fund. The result is then also weighted by the company\'s weight in that fund.',
                 'holdings_date': 'Date of the fund holdings to use, YYYY-MM-DD. Default: the latest at the cutoff.',
                 'holding_weight': 'The weight to use instead of looking it up, 0.24 means 24%. Not together with etf_ref.'}},

    'get_financial_observations': {
        'summary': 'Read reported company financials: revenue, operating income, EPS and BPS.',
        'returns': 'A table with one figure per fiscal year, fiscalPeriod (Q1 to Q4, FY), periodKind (QUARTER is that three months alone, CUMULATIVE is from the start of the year) '
                   'and fsBasis (CFS consolidated, OFS separate). value_text is the amount as written. Say which period and basis the answer uses. '
                   'There are no analyst consensus figures and no segment figures in this data.',
        'prerequisite': 'Company or security ids from a search, or a get_etf_holdings result as targets.',
        'follow_up': 'compare_two_figures for a change between two rows. A fourth quarter alone is a stored row: fiscalPeriod Q4, periodKind QUARTER, '
                     'derivation FY_MINUS_9M, which says it was calculated as the full year minus nine months and not reported.',
        'args': {'targets': TARGETS + ' A security is mapped to its issuing company.',
                 'kind': 'actual for reported figures. guidance returns reported events, not numbers. consensus and segment return nothing in this data.',
                 'fiscal_years': 'Years to read, as [2026].', 'metrics': 'revenue, operating_income, eps_basic, eps_diluted, bps, bps_total_shares.',
                 'fs_basis': 'CFS or OFS.', 'limit': LIMIT}},
    'compare_two_figures': {
        'summary': 'Calculate the difference and the change ratio between two figures of one financial or macro result.',
        'returns': 'One row with the difference as written and change_ratio. It refuses two figures that differ in company, metric, unit, accounting basis or period kind, '
                   'and says which one differs. derivations is given when one figure was reported and the other calculated: say so.',
        'prerequisite': 'A get_financial_observations or get_macro_observations result. The ids are the object_id of two rows.',
        'args': {'dataset_ref': RESULT, 'earlier_id': 'object_id of the earlier figure.', 'later_id': 'object_id of the later figure.'}},
    'compare_actual_to_expectation': {
        'summary': 'Compare a reported figure with an expectation published before it.',
        'returns': 'The difference, only when both figures have the same metric, period, unit and accounting basis. '
                   'This data has no comparable expectation figures, so the call normally reports that the comparison is not possible.',
        'prerequisite': 'An actual figure and an expectation figure from get_financial_observations.',
        'args': {'actual_ref': RESULT, 'actual_id': 'object_id of the reported figure.', 'expectation_ref': RESULT, 'expectation_id': 'object_id of the expectation.'}},
    'list_macro_series': {
        'summary': 'List the macro series this data has, with their ids, units and date range.',
        'returns': 'One row per series. Names are English. A name search that fails does not mean the series is missing: read this list.',
        'follow_up': 'get_macro_observations with the series ids.', 'args': {}},
    'get_macro_observations': {
        'summary': 'Read the values of macro series over a period: exchange rate, yields, oil price, inflation.',
        'returns': 'A table with one row per series and day, with unit and source. No causation is implied.',
        'prerequisite': 'series ids from list_macro_series.',
        'follow_up': 'compare_two_figures for a change between two days.',
        'args': {'start_date': START, 'end_date': END, 'series_ids': 'Ids from list_macro_series, as ["usd_krw"].',
                 'query': 'Part of an English series name. Prefer series_ids.', 'limit': LIMIT}},

    'get_security_classifications': {
        'summary': 'Read the industry, exchange classification, listing and market capitalization records of securities.',
        'returns': 'A table with one row per security and record. Two providers classify the same security differently; a security without a record has no row.',
        'prerequisite': 'Security ids from a search, or a get_etf_holdings result as targets.',
        'args': {'targets': TARGETS, 'factors': 'industry is the FMP sector and industry. exchange_classification is the Korean exchange classification. Ask for both to compare providers.',
                 'limit': LIMIT}},
    'get_instrument_factors': {
        'summary': 'Read prices, daily flows, valuation inputs and macro values for securities in one call.',
        'returns': 'A table of the raw observations with a factor column. Nothing is scored or calculated.',
        'prerequisite': 'Security ids from a search, or a get_etf_holdings result as targets.',
        'alternative': 'get_price_observations, get_flow_observations, get_financial_observations and get_macro_observations read the same data one kind at a time.',
        'args': {'targets': TARGETS, 'start_date': START, 'end_date': END, 'factors': 'chart is prices, flow is daily flows, valuation is EPS and BPS, macro is the macro series.',
                 'limit': LIMIT}},
    'compare_etf_overlap': {
        'summary': 'Compare the holdings of two funds on the same date: what both hold and how much the weights overlap.',
        'returns': 'A table with each holding, its weight in either fund and overlap_weight (the smaller of the two). Shared holdings come first. '
                   'data_scope.overlap_weight is the total overlap and shared_count the number held by both.',
        'prerequisite': 'Two get_etf_holdings results with the same selected_date.',
        'args': {'left_ref': RESULT, 'right_ref': RESULT, 'group_by': 'security compares share classes. company joins the share classes of one issuer.'}},

    'sum_investor_flows': {
        'summary': 'Add one investor\'s daily net flow over the days of an earlier flow result, per security.',
        'returns': 'One row per security with the total as written, the first and last day covered, and how many days had no value.',
        'prerequisite': 'A daily get_flow_observations result.',
        'args': {'dataset_ref': RESULT, 'field': 'Which investor and unit: netVal... is money, netQty... is shares.'}},

    'get_previous_reports': {
        'summary': 'Read the outlook reports and price explanations published earlier for one fund.',
        'returns': 'A table with one row per report: when it was analysed, when it was published (availableAt) and its summary. '
                   'These are earlier interpretations, not observed facts: do not cite one as evidence of a new fact.',
        'prerequisite': 'The fund id from search_objects or find_securities_by_ticker.',
        'args': {'etf_ref': 'The fund, as {"object_type":"ETF","object_id":"..."}.',
                 'before': 'Only reports analysed and published before this time, with a timezone. A time after the cutoff means the cutoff.',
                 'kind': 'outlook, movement (price explanations), or both.', 'limit': LIMIT}},
}


def text(name):
    """The description of one tool, in the standard order."""
    entry = TOOLS[name]
    lines = [entry['summary']] + [label + ': ' + entry[key] for key, label in
        (('returns', 'Returns'), ('prerequisite', 'Prerequisite'), ('follow_up', 'Follow-up'), ('alternative', 'Alternative')) if entry.get(key)]
    return '\n'.join(lines)
