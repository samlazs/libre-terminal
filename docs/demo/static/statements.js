/* Financial statements (income statement, balance sheet, cash flow) as a statement: line items down the side in
 * statement order, periods across (newest first), one money unit for the whole statement, subtotals emphasised.
 * Sources name their lines differently, so each has its own ordered line map; lines a map does not know are listed
 * under "Other items", so nothing the source sent is hidden. No computed rows here (Growth & Ratios has those). */
(function (T) {
  "use strict";
  var el = T.el;

  /**
   * Ordered line maps per source and statement. Tokens: "#Section_name"; a field, then flags: "!" total, "+" subtotal,
   * "-" detail (indented); "?other" = hide when `other` has data (the same figure under another name);
   * "&other" = show only when `other` has data (a continuing-operations line is only useful next to a discontinued one).
   */
  var MAPS = {
    yfinance: {
      income: "operating_revenue- total_revenue! interest_income-?interest_income_non_operating interest_expense-?interest_expense_non_operating " +
        "net_interest_income+?net_non_operating_interest_income_expense cost_of_revenue gross_profit+ net_policyholder_benefits_and_claims " +
        "policyholder_benefits_gross- policyholder_benefits_ceded- loss_adjustment_expense- " +
        "#Operating_expenses salaries_and_wages- professional_expense_and_contract_services_expense- occupancy_and_equipment- selling_and_marketing_expense- " +
        "general_and_administrative_expense- other_gand_a- selling_general_and_admin_expense research_and_development_expense " +
        "depreciation_and_amortization_in_income_statement- depreciation_income_statement- amortization- provision_for_doubtful_accounts- " +
        "other_non_interest_expense other_operating_expenses operating_expense+ operating_income! " +
        "#Non-operating_items_and_net_income interest_income_non_operating- interest_expense_non_operating- net_non_operating_interest_income_expense " +
        "gain_on_sale_of_security- gain_on_sale_of_business- gain_on_sale_of_ppe- earnings_from_equity_interest- earnings_from_equity_interest_net_of_tax- " +
        "restructuring_and_mergern_acquisition- impairment_of_capital_assets- write_off- other_special_charges- special_income_charges " +
        "other_non_operating_income_expenses- other_income_expense total_pre_tax_income! tax_provision net_income_continuous_operations+ " +
        "net_income_discontinuous_operations- net_income_extraordinary- net_income_including_noncontrolling_interests minority_interests- net_income! " +
        "preferred_stock_dividends- otherunder_preferred_stock_dividend- net_income_attributable_to_common_shareholders+ " +
        "#Per_share basic_earnings_per_share diluted_earnings_per_share+ weighted_average_basic_shares_outstanding weighted_average_diluted_shares_outstanding " +
        "#Supplemental_(Yahoo_Finance) ebit ebitda+ normalized_ebitda normalized_income total_expenses total_operating_income_as_reported reconciled_cost_of_revenue " +
        "reconciled_depreciation total_unusual_items total_unusual_items_excluding_goodwill tax_effect_of_unusual_items tax_rate_for_calcs " +
        "diluted_ni_availto_com_stockholders- net_income_from_continuing_and_discontinued_operation- net_income_from_continuing_operation_net_minority_interest- net_income_common_stockholders-",
      balance: "#Assets cash_financial- cash_equivalents- cash_and_cash_equivalents+ cash_cash_equivalents_and_federal_funds_sold short_term_investments- " +
        "other_short_term_investments- cash_cash_equivalents_and_short_term_investments+ accounts_receivable- allowance_for_doubtful_accounts_receivable- " +
        "other_receivables- net_receivables net_loan raw_materials- work_in_process- finished_goods- other_inventories- inventories prepaid_assets- restricted_cash- " +
        "assets_held_for_sale_current- hedging_assets_current- other_current_assets total_current_assets+ properties- land_and_improvements- buildings_and_improvements- " +
        "machinery_furniture_equipment- construction_in_progress- other_properties- leases- plant_property_equipment_gross accumulated_depreciation- plant_property_equipment_net+ " +
        "goodwill- other_intangible_assets- goodwill_and_other_intangible_assets trading_securities- available_for_sale_securities- held_to_maturity_securities- " +
        "investmentin_financial_assets- long_term_equity_investment- investments_in_other_ventures_under_equity_method- other_investments- investments_and_advances " +
        "non_current_accounts_receivable- non_current_note_receivables- non_current_deferred_taxes_assets- non_current_deferred_assets- defined_pension_benefit- " +
        "non_current_prepaid_assets- other_non_current_assets total_non_current_assets+ total_assets! " +
        "#Liabilities accounts_payable- income_tax_payable- total_tax_payable- dividends_payable- other_payable- payables current_accrued_expenses- interest_payable- " +
        "payables_and_accrued_expenses commercial_paper- other_current_borrowings- current_notes_payable- current_debt current_capital_lease_obligation- " +
        "current_debt_and_capital_lease_obligation current_deferred_revenue- current_deferred_liabilities pension_and_other_post_retirement_benefit_plans_current- " +
        "other_current_liabilities current_liabilities+ long_term_debt- long_term_capital_lease_obligation- long_term_debt_and_capital_lease_obligation " +
        "non_current_deferred_revenue- non_current_deferred_taxes_liabilities- non_current_deferred_liabilities tradeand_other_payables_non_current- employee_benefits- " +
        "non_current_pension_and_other_postretirement_benefit_plans- preferred_securities_outside_stock_equity- other_non_current_liabilities " +
        "total_non_current_liabilities_net_minority_interest+ total_liabilities_net_minority_interest! " +
        "#Equity preferred_stock- common_stock- capital_stock additional_paid_in_capital retained_earnings treasury_stock other_equity_adjustments- " +
        "gains_losses_not_affecting_retained_earnings preferred_stock_equity- common_stock_equity- total_common_equity+ minority_interest- total_equity_non_controlling_interests! " +
        "#Supplemental_(Yahoo_Finance) total_capitalization invested_capital working_capital tangible_book_value net_tangible_assets total_debt+ net_debt " +
        "capital_lease_obligations share_issued ordinary_shares_number preferred_shares_number treasury_shares_number",
      cash: "#Operating_activities net_income_from_continuing_operations depreciation_amortization_depletion depreciation_and_amortization?depreciation_amortization_depletion " +
        "deferred_tax deferred_income_tax?deferred_tax stock_based_compensation provision_and_write_off_of_assets- asset_impairment_charge- gain_loss_on_investment_securities- " +
        "gain_loss_on_sale_of_ppe- other_non_cash_items changes_in_account_receivables- change_in_receivables- change_in_inventory- change_in_prepaid_assets- " +
        "change_in_account_payable- change_in_payable- change_in_payables_and_accrued_expense- change_in_accrued_expense- change_in_other_current_assets- " +
        "change_in_other_current_liabilities- change_in_other_working_capital- change_in_working_capital+ " +
        "cash_flow_from_continuing_operating_activities?operating_cash_flow operating_cash_flow! " +
        "#Investing_activities capital_expenditure purchase_of_ppe-?capital_expenditure sale_of_ppe- investments_in_property_plant_and_equipment-?capital_expenditure " +
        "net_ppe_purchase_and_sale-?capital_expenditure purchase_of_business- sale_of_business- net_business_purchase_and_sale purchase_of_investment- sale_of_investment- " +
        "net_investment_purchase_and_sale net_other_investing_changes cash_flow_from_continuing_investing_activities?investing_cash_flow investing_cash_flow! " +
        "#Financing_activities long_term_debt_issuance- long_term_debt_payments- net_long_term_debt_issuance- net_short_term_debt_issuance- net_issuance_payments_of_debt " +
        "common_stock_issuance- common_stock_payments- repurchase_of_common_equity- net_common_stock_issuance preferred_stock_dividend_paid- common_stock_dividend_paid- " +
        "cash_dividends_paid net_other_financing_charges cash_flow_from_continuing_financing_activities?financing_cash_flow financing_cash_flow! " +
        "#Change_in_cash effect_of_exchange_rate_changes changes_in_cash+ net_change_in_cash_and_equivalents+?changes_in_cash beginning_cash_position end_cash_position+ " +
        "#Supplemental_(Yahoo_Finance) free_cash_flow+ income_tax_paid_supplemental_data interest_paid_supplemental_data issuance_of_debt repayment_of_debt " +
        "issuance_of_capital_stock repurchase_of_capital_stock"
    },
    sec: {
      income: "operating_revenue- other_revenue- total_revenue! operating_cost_of_revenue- other_cost_of_revenue- total_cost_of_revenue excise_and_sales_taxes " +
        "total_gross_profit+ sga_expense- rd_expense- exploration_expense- depreciation_expense- amortization_expense- depreciation_and_amortization- depletion_expense- " +
        "other_operating_expenses- impairment_expense- restructuring_charge- other_special_charges- total_operating_expenses+ costs_and_expenses total_operating_income! " +
        "#Interest_income_and_expense loans_and_lease_interest_income- investment_securities_interest_income- deposits_interest_income- fed_funds_and_repo_interest_income- " +
        "trading_account_interest_income- other_interest_income- total_interest_income deposits_interest_expense- short_term_borrowing_interest_expense- " +
        "long_term_debt_interest_expense- fed_funds_and_repo_interest_expense- capitalized_lease_obligation_interest_expense- other_interest_expense- " +
        "total_interest_expense net_interest_income+ " +
        "#Non-interest_income_and_expense revenues_excl_interest_dividends trust_fee_income- service_charges_on_deposits_income- other_service_charge_income- " +
        "net_realized_capital_gains- premiums_earned- investment_banking_income- other_noninterest_income- total_noninterest_income+ provision_for_credit_losses " +
        "net_interest_income_after_provision+ benefits_costs_expenses current_and_future_benefits- salaries_and_employee_benefits_expense- net_occupancy_equipment_expense- " +
        "marketing_expense- property_liability_insurance_claims- policy_acquisition_costs- amortization_of_deferred_policy_acquisition_costs- total_noninterest_expense+ " +
        "#Pre-tax_and_net_income non_operating_income- other_income- other_gains- total_other_income total_pretax_income! income_tax_expense income_tax_current- " +
        "income_tax_deferred- income_before_equity_method equity_method_investments- net_income_continuing+ net_income_discontinued- extraordinary_income- " +
        "other_adjustments_to_consolidated_net_income- gain_on_sale_properties- gain_loss_disposition_subsidiary- net_income! preferred_dividends- " +
        "net_income_nci_redeemable- net_income_nci_nonredeemable- net_income_to_noncontrolling_interest- other_adjustments_to_net_income_to_common- net_income_to_common+ " +
        "#Per_share basic_eps diluted_eps+ basic_diluted_eps cash_dividends_per_share weighted_ave_basic_shares_os weighted_ave_diluted_shares_os weighted_ave_basic_diluted_shares_os " +
        "#Comprehensive_income other_comprehensive_income_parent comprehensive_income_parent other_comprehensive_income_nci comprehensive_income_nci comprehensive_income+",
      balance: "#Assets cash_and_equivalents restricted_cash- short_term_investments fed_funds_sold- interest_bearing_deposits_at_other_banks- time_deposits_placed- " +
        "trading_account_securities loans_and_leases allowance_for_loan_and_lease_losses- net_loans_and_leases+ loans_held_for_sale- accrued_investment_income- " +
        "accounts_receivable customer_and_other_receivables- note_receivable- nontrade_receivables- net_inventory prepaid_expenses- current_deferred_tax_assets- " +
        "other_current_assets other_current_nonoperating_assets- total_current_assets+ gross_ppe accumulated_depreciation- net_ppe+ net_premises_and_equipment " +
        "operating_lease_right_of_use_asset- finance_lease_right_of_use_asset- long_term_investments mortgage_servicing_rights- deferred_acquisition_cost- " +
        "separate_account_business_assets- noncurrent_note_receivables- goodwill intangible_assets noncurrent_deferred_tax_assets- employee_benefit_assets- " +
        "other_noncurrent_assets other_noncurrent_nonoperating_assets- other_noncurrent_assets_excl_ppe- total_noncurrent_assets+ other_assets total_assets! " +
        "#Liabilities noninterest_bearing_deposits- interest_bearing_deposits- fed_funds_purchased- short_term_debt current_portion_of_long_term_debt bankers_acceptances- " +
        "accounts_payable accrued_interest_payable- other_short_term_payables- accrued_expenses customer_deposits- dividends_payable- current_deferred_revenue " +
        "current_deferred_tax_liabilities- current_employee_benefit_liabilities- other_taxes_payable- other_current_liabilities other_current_nonoperating_liabilities- " +
        "operating_lease_liability_current- finance_lease_liability_current- total_current_liabilities+ long_term_debt capital_lease_obligations- " +
        "operating_lease_liability_noncurrent- claims_and_claim_expenses- future_policy_benefits- unearned_premiums_credit- policyholder_funds- " +
        "participating_policyholder_equity- separate_account_business_liabilities- other_long_term_liabilities asset_retirement_and_litigation_obligation- " +
        "noncurrent_deferred_revenue- noncurrent_deferred_tax_liabilities- noncurrent_employee_benefit_liabilities- other_noncurrent_liabilities " +
        "other_noncurrent_nonoperating_liabilities- total_noncurrent_liabilities+ total_liabilities! commitments_and_contingencies- temporary_equity- " +
        "temporary_equity_parent- redeemable_noncontrolling_interest- redeemable_nci_common- redeemable_nci_preferred- redeemable_nci_other- " +
        "#Equity total_preferred_equity common_equity- additional_paid_in_capital- retained_earnings treasury_stock- accumulated_other_comprehensive_income- other_equity- " +
        "total_common_equity+ total_equity+ noncontrolling_interests- total_equity_and_noncontrolling_interests+ total_liabilities_and_equity!",
      cash: "#Operating_activities net_income net_income_discontinued- net_income_continuing-&net_income_discontinued provision_for_loan_losses depreciation_expense- " +
        "amortization_expense- depreciation_and_amortization stock_based_compensation deferred_income_tax gain_loss_on_investments- gain_loss_on_sale_of_assets- " +
        "income_loss_from_equity_method_investments- asset_impairment_charges- noncash_adjustments_to_net_income- other_operating_activities change_in_insurance_reserves- " +
        "change_in_accounts_receivable- change_in_nontrade_receivables- change_in_inventories- change_in_accounts_payable- change_in_other_operating_assets_and_liabilities- " +
        "increase_decrease_in_operating_capital+ net_cash_from_continuing_operating_activities-&net_cash_from_discontinued_operating_activities " +
        "net_cash_from_discontinued_operating_activities- net_cash_from_operating_activities! " +
        "#Investing_activities purchase_of_plant_property_and_equipment acquisitions purchase_of_investments purchase_of_held_to_maturity_investments- " +
        "sale_of_plant_property_and_equipment- sale_of_productive_assets- divestitures sale_of_investments maturity_of_investments- net_increase_in_fed_funds_sold- " +
        "loans_held_for_sale_net- other_investing_activities_net net_cash_from_continuing_investing_activities-&net_cash_from_discontinued_investing_activities " +
        "net_cash_from_discontinued_investing_activities- net_cash_from_investing_activities! " +
        "#Financing_activities repayment_of_debt repurchase_of_preferred_equity- repurchase_of_common_equity payment_of_dividends issuance_of_debt " +
        "issuance_of_preferred_equity- issuance_of_common_equity net_change_in_deposits- short_term_debt_net tax_withholding_share_based_compensation- " +
        "other_financing_activities_net net_cash_from_continuing_financing_activities-&net_cash_from_discontinued_financing_activities " +
        "net_cash_from_discontinued_financing_activities- net_cash_from_financing_activities! " +
        "#Change_in_cash effect_of_exchange_rate_changes net_cash_from_discontinued_operations- other_net_changes_in_cash- net_change_in_cash+ " +
        "cash_at_beginning_of_period cash_at_end_of_period+ " +
        "#Supplemental cash_interest_paid cash_interest_received cash_income_taxes_paid"
    }
  };

  var LABELS = {
    total_revenue: "Total revenue", operating_revenue: "Operating revenue", cost_of_revenue: "Cost of revenue", total_cost_of_revenue: "Cost of revenue",
    gross_profit: "Gross profit", total_gross_profit: "Gross profit", selling_general_and_admin_expense: "SG&A", sga_expense: "SG&A",
    research_and_development_expense: "R&D", rd_expense: "R&D", operating_expense: "Total operating expenses", total_operating_expenses: "Total operating expenses",
    operating_income: "Operating income", total_operating_income: "Operating income", total_pre_tax_income: "Pre-tax income", total_pretax_income: "Pre-tax income",
    tax_provision: "Income tax", income_tax_expense: "Income tax", net_income: "Net income",
    net_income_attributable_to_common_shareholders: "Net income to common shareholders", net_income_to_common: "Net income to common shareholders",
    net_income_continuous_operations: "Net income from continuing operations", net_income_continuing: "Net income from continuing operations",
    net_income_including_noncontrolling_interests: "Net income incl. minority interests", minority_interests: "Minority interests",
    basic_earnings_per_share: "EPS, basic", basic_eps: "EPS, basic", diluted_earnings_per_share: "EPS, diluted", diluted_eps: "EPS, diluted",
    basic_diluted_eps: "EPS, basic and diluted", cash_dividends_per_share: "Dividends per share",
    weighted_average_basic_shares_outstanding: "Shares, basic (average)", weighted_ave_basic_shares_os: "Shares, basic (average)",
    weighted_average_diluted_shares_outstanding: "Shares, diluted (average)", weighted_ave_diluted_shares_os: "Shares, diluted (average)",
    weighted_ave_basic_diluted_shares_os: "Shares, basic and diluted (average)",
    ebit: "EBIT", ebitda: "EBITDA", normalized_ebitda: "Normalized EBITDA", other_income_expense: "Other income / expense",
    other_non_operating_income_expenses: "Other non-operating income / expense", net_non_operating_interest_income_expense: "Net non-operating interest",
    interest_income_non_operating: "Interest income", interest_expense_non_operating: "Interest expense", special_income_charges: "Special income / charges",
    restructuring_and_mergern_acquisition: "Restructuring and M&A", tax_rate_for_calcs: "Tax rate used by Yahoo Finance",
    diluted_ni_availto_com_stockholders: "Diluted net income to common", otherunder_preferred_stock_dividend: "Other preferred dividends", other_gand_a: "Other G&A",
    total_operating_income_as_reported: "Operating income as reported", net_policyholder_benefits_and_claims: "Policyholder benefits and claims",
    cash_financial: "Cash", cash_and_cash_equivalents: "Cash and equivalents", cash_and_equivalents: "Cash and equivalents",
    cash_cash_equivalents_and_short_term_investments: "Cash and short-term investments", cash_cash_equivalents_and_federal_funds_sold: "Cash, equivalents and fed funds sold",
    net_receivables: "Receivables", inventories: "Inventories", net_inventory: "Inventories", total_current_assets: "Total current assets",
    plant_property_equipment_gross: "PP&E, gross", gross_ppe: "PP&E, gross", plant_property_equipment_net: "PP&E, net", net_ppe: "PP&E, net",
    goodwill_and_other_intangible_assets: "Goodwill and intangibles", investmentin_financial_assets: "Financial assets", net_loan: "Net loans",
    total_non_current_assets: "Total non-current assets", total_noncurrent_assets: "Total non-current assets", total_assets: "Total assets",
    current_liabilities: "Total current liabilities", total_current_liabilities: "Total current liabilities",
    total_non_current_liabilities_net_minority_interest: "Total non-current liabilities", total_noncurrent_liabilities: "Total non-current liabilities",
    total_liabilities_net_minority_interest: "Total liabilities", total_liabilities: "Total liabilities",
    gains_losses_not_affecting_retained_earnings: "Accumulated other comprehensive income", total_common_equity: "Shareholders' equity",
    common_stock_equity: "Common equity", total_equity_non_controlling_interests: "Total equity incl. minority interests",
    total_equity_and_noncontrolling_interests: "Total equity incl. minority interests", total_liabilities_and_equity: "Total liabilities and equity",
    tradeand_other_payables_non_current: "Trade and other payables, non-current", non_current_deferred_taxes_assets: "Deferred tax assets",
    share_issued: "Shares issued", ordinary_shares_number: "Shares outstanding", treasury_shares_number: "Treasury shares", preferred_shares_number: "Preferred shares",
    operating_cash_flow: "Operating cash flow", net_cash_from_operating_activities: "Operating cash flow", investing_cash_flow: "Investing cash flow",
    net_cash_from_investing_activities: "Investing cash flow", financing_cash_flow: "Financing cash flow", net_cash_from_financing_activities: "Financing cash flow",
    capital_expenditure: "Capital expenditure", free_cash_flow: "Free cash flow", depreciation_amortization_depletion: "Depreciation and amortization",
    net_income_from_continuing_operations: "Net income (continuing operations)", change_in_working_capital: "Change in working capital",
    net_business_purchase_and_sale: "Acquisitions and disposals, net", net_investment_purchase_and_sale: "Investments bought and sold, net",
    net_other_investing_changes: "Other investing", net_issuance_payments_of_debt: "Debt issued / repaid, net", net_common_stock_issuance: "Shares issued / bought back, net",
    cash_dividends_paid: "Dividends paid", payment_of_dividends: "Dividends paid", net_other_financing_charges: "Other financing",
    beginning_cash_position: "Cash at beginning of period", cash_at_beginning_of_period: "Cash at beginning of period",
    end_cash_position: "Cash at end of period", cash_at_end_of_period: "Cash at end of period", changes_in_cash: "Net change in cash", net_change_in_cash: "Net change in cash",
    net_change_in_cash_and_equivalents: "Net change in cash", effect_of_exchange_rate_changes: "Effect of exchange rates",
    income_tax_paid_supplemental_data: "Income taxes paid", interest_paid_supplemental_data: "Interest paid", increase_decrease_in_operating_capital: "Change in working capital"
  };
  var KEYS = /^(period_ending|period_beginning|fiscal_period|fiscal_year|reported_currency|symbol|cik|filing_date|accepted_date|calendar_year|period|currency|form|url|link|date)$/;

  function parse(spec) {
    var out = [], sec = null;
    String(spec || "").split(/\s+/).filter(Boolean).forEach(function (tok) {
      if (tok.charAt(0) === "#") { sec = tok.slice(1).replace(/_/g, " "); return; }
      var m = /^([a-z0-9_]+)([!+\-]*)(?:([?&])([a-z0-9_]+))?$/.exec(tok);
      if (!m) return;
      out.push({ field: m[1], section: sec, em: m[2].indexOf("!") >= 0 ? "tot" : m[2].indexOf("+") >= 0 ? "sub" : null, ind: m[2].indexOf("-") >= 0,
        hideIf: m[3] === "?" ? m[4] : null, onlyIf: m[3] === "&" ? m[4] : null });
    });
    return out;
  }
  function sentence(field) {
    var words = T.prettyField(field).split(" ");
    return words.map(function (w, i) { return i === 0 || /[A-Z].*[A-Z&]|&/.test(w) ? w : w.toLowerCase(); }).join(" ");
  }
  function labelOf(field) { return LABELS[field] || sentence(field); }
  function kindOf(field) {
    if (/(^|_)(eps|earnings_per_share|per_share)($|_)/.test(field)) return "eps";
    if (/shares|_os$|share_issued/.test(field)) return "shares";
    if (/^(tax_rate_for_calcs|effective_tax_rate)$/.test(field)) return "rate";
    return "money";
  }
  function has(rows, f) { return rows.some(function (r) { var v = r[f]; return typeof v === "number" ? isFinite(v) : v != null && v !== ""; }); }

  /** "income" | "balance" | "cash" for the statement endpoints (any source), else null. */
  T.statementKind = function (def) {
    var m = /\/equity\/fundamental\/(income|balance|cash)$/.exec(String(def && def.endpoint || ""));
    return m ? m[1] : null;
  };

  /**
   * The ordered lines of a statement: [{field, label, section, em, ind, kind}] for the lines with data in at least one
   * period. A source without a map keeps its declared order (then the order of the first row). Unknown lines go last.
   */
  T.statementLines = function (kind, provider, rows, declared) {
    var src = MAPS[provider] && MAPS[provider][kind] ? parse(MAPS[provider][kind]) : null;
    var seen = {}, out = [];
    var known = src || (declared || []).concat(Object.keys(rows[0] || {})).map(function (f) { return { field: f, section: null, em: null, ind: false }; });
    known.forEach(function (l) {
      if (seen[l.field] || KEYS.test(l.field)) return;
      seen[l.field] = 1;
      if (!has(rows, l.field) || (l.hideIf && has(rows, l.hideIf)) || (l.onlyIf && !has(rows, l.onlyIf))) return;
      if (!rows.some(function (r) { return typeof r[l.field] === "number"; })) return;          // text fields are not statement lines
      out.push({ field: l.field, label: labelOf(l.field), section: l.section, em: l.em, ind: l.ind, kind: kindOf(l.field) });
    });
    var other = [];
    rows.forEach(function (r) { Object.keys(r).forEach(function (f) { if (!seen[f] && !KEYS.test(f) && f.charAt(0) !== "_") { seen[f] = 1; other.push(f); } }); });
    (declared || []).forEach(function (f) { if (!seen[f] && !KEYS.test(f)) { seen[f] = 1; other.push(f); } });
    other.forEach(function (f) {
      if (!rows.some(function (r) { return typeof r[f] === "number" && isFinite(r[f]); })) return;
      out.push({ field: f, label: labelOf(f), section: src ? "Other items" : null, em: null, ind: false, kind: kindOf(f) });
    });
    return out;
  };

  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  // rate lines (tax rate) by source: Yahoo sends fractions (0.21)
  var RATE_FMT = { yfinance: "normalizedPercent" };
  /**
   * Column headings: FY2026 (annual), Q3 2026 / Sep 2026 (quarterly), TTM Sep 2026; newest first. SEC's fiscal_year is the
   * year of the filing that last reported the figure (ORCL's FY ending May 2024 carries 2026), so SEC columns are labelled
   * from the period end.
   */
  T.statementPeriods = function (rows, period, provider) {
    var quarterly = /^quarter/.test(String(period || "")), ttm = /^ttm$/i.test(String(period || ""));
    var byEnd = provider === "sec";
    var list = rows.filter(function (r) { return r && r.period_ending; }).slice().sort(function (a, b) { return String(b.period_ending).localeCompare(String(a.period_ending)); });
    var used = {};
    return list.map(function (r) {
      var d = String(r.period_ending).slice(0, 10), y = d.slice(0, 4), mon = MON[+d.slice(5, 7) - 1] || "", fp = String(r.fiscal_period || "").toUpperCase();
      var label = ttm ? "TTM " + mon + " " + y
        : quarterly ? (!byEnd && /^Q[1-4]$/.test(fp) && r.fiscal_year ? fp + " " + r.fiscal_year : mon + " " + y)
        : "FY" + (!byEnd && r.fiscal_year && /^\d{4}$/.test(String(r.fiscal_year)) ? r.fiscal_year : y);
      if (used[label]) label += " (" + d + ")";
      used[label] = 1;
      return { row: r, label: label, tip: "Period ending " + d };
    });
  };

  // Stage 3 8c: common-size and year-over-year views of a statement (client side, any source)
  var REV = ["total_revenue", "revenue", "revenues", "total_revenues", "operating_revenue", "net_revenue"];
  function fin(v) { return typeof v === "number" && isFinite(v) ? v : null; }
  /** Common-size base of one period: total assets (balance sheet), else revenue (income statement); a bank without a
   *  revenue line: net interest income + non-interest income when both are reported; else null. A cash flow statement
   *  carries no revenue line, so it has no common-size view. */
  T.statementBase = function (kind, row) {
    if (!row || kind === "cash") return null;
    if (kind === "balance") return fin(row.total_assets);
    for (var i = 0; i < REV.length; i++) if (fin(row[REV[i]]) != null) return fin(row[REV[i]]);
    var nii = fin(row.net_interest_income), non = fin(row.total_noninterest_income) != null ? fin(row.total_noninterest_income) : fin(row.non_interest_income);
    return nii != null && non != null ? nii + non : null;
  };
  /** The same period one year earlier (period end 335 to 395 days before), from all periods the source sent; else null. */
  T.statementYearAgo = function (periods, row) {
    var t = Date.parse(String(row && row.period_ending || "").slice(0, 10));
    if (!isFinite(t)) return null;
    for (var i = 0; i < periods.length; i++) {
      var d = (t - Date.parse(String(periods[i].row.period_ending).slice(0, 10))) / 864e5;
      if (d >= 335 && d <= 395) return periods[i].row;
    }
    return null;
  };
  /** Year-over-year change in percent; "n/m" when the base is zero or negative or the sign flips; null when a figure is missing. */
  T.statementChange = function (cur, base) {
    cur = fin(cur); base = fin(base);
    if (cur == null || base == null) return null;
    if (base <= 0 || cur < 0) return "n/m";
    return (cur / base - 1) * 100;
  };
  var PCT = { scale: 0, scaleUnit: "", formatterFn: "percent", decimalPlaces: 1, paren: false };

  /** One money unit for the whole statement, chosen from its largest figure. */
  T.statementUnit = function (maxAbs) {
    if (maxAbs >= 1e9) return { scale: 1e6, dp: 0, label: "mn" };
    if (maxAbs >= 1e6) return { scale: 1e6, dp: 1, label: "mn" };
    if (maxAbs >= 1e4) return { scale: 1e3, dp: 1, label: "thousands" };
    return { scale: 1, dp: 0, label: "" };
  };

  /**
   * T.statement(host, {data, colDefs, kind, provider, period, periodOptions, onPeriod(value), onCount(text)})
   * Returns the table object (T.table) with .statement = true.
   */
  T.statement = function (host, opts) {
    var rows = T.normalizeRows(opts.data).filter(function (r) { return r && typeof r === "object"; });
    var declared = (opts.colDefs || []).map(function (d) { return d && d.field; }).filter(Boolean);
    var periods = T.statementPeriods(rows, opts.period, opts.provider), allPeriods = periods.length;
    // sources without a limit send decades of quarters: the latest ones first, the rest one click away
    var cap = /^quarter/.test(String(opts.period || "")) ? 12 : 10;
    if (!opts.all && periods.length > cap) periods = periods.slice(0, cap);
    var lines = T.statementLines(opts.kind, opts.provider, periods.map(function (p) { return p.row; }), declared);
    var maxMoney = 0, maxShares = 0;
    lines.forEach(function (l) {
      periods.forEach(function (p) {
        var v = p.row[l.field];
        if (typeof v !== "number" || !isFinite(v)) return;
        if (l.kind === "money") maxMoney = Math.max(maxMoney, Math.abs(v));
        else if (l.kind === "shares") maxShares = Math.max(maxShares, Math.abs(v));
      });
    });
    var show = opts.show === "common" || opts.show === "yoy" ? opts.show : "values";
    var bases = periods.map(function (p) { return T.statementBase(opts.kind, p.row); });
    var canCommon = bases.some(function (b) { return b != null && b > 0; });
    var everyP = T.statementPeriods(rows, opts.period, opts.provider);       // a year-ago period may be past the ones shown
    var prior = periods.map(function (p) { return T.statementYearAgo(everyP, p.row); });
    var canYoy = prior.some(Boolean);
    if ((show === "common" && !canCommon) || (show === "yoy" && !canYoy)) show = "values";
    var unit = T.statementUnit(maxMoney), sunit = maxShares >= 1e6 ? { scale: 1e6, dp: maxShares >= 1e9 ? 0 : 1, label: "mn" } : { scale: 1, dp: 0, label: "" };
    var ccy = (rows.filter(function (r) { return r.reported_currency || r.currency; })[0] || {});
    ccy = String(ccy.reported_currency || ccy.currency || "").toUpperCase();
    var data = [], lastSec = null;
    lines.forEach(function (l) {
      if (l.section && l.section !== lastSec) { data.push({ _section: l.section }); lastSec = l.section; }
      var r = { line: l.label + (l.kind === "shares" && sunit.label ? " (" + sunit.label + ")" : ""), _fmt: {} };
      if (l.em) r._em = l.em;
      if (l.ind) r._ind = true;
      periods.forEach(function (p, j) {
        var v = p.row[l.field];
        r["p" + j] = typeof v === "number" && isFinite(v) ? v : null;
        if (show === "common") {                     // money lines only: per-share, share counts and rates have no common size
          r["p" + j] = l.kind === "money" && r["p" + j] != null && bases[j] > 0 ? r["p" + j] / bases[j] * 100 : null;
          r._fmt["p" + j] = PCT;
          return;
        }
        if (show === "yoy") {
          r["p" + j] = l.kind === "rate" || !prior[j] ? null : T.statementChange(r["p" + j], prior[j][l.field]);
          r._fmt["p" + j] = PCT;
          return;
        }
        if (l.kind === "eps") r._fmt["p" + j] = { scale: 0, decimalPlaces: 2 };
        else if (l.kind === "shares") r._fmt["p" + j] = { scale: sunit.scale === 1 ? 0 : sunit.scale, scaleUnit: "", decimalPlaces: sunit.dp };
        // a rate's unit is the source's, never guessed from the value (a 130% tax rate is 1.3 as a fraction); unknown: as delivered
        else if (l.kind === "rate") r._fmt["p" + j] = RATE_FMT[opts.provider] ? { scale: 0, formatterFn: RATE_FMT[opts.provider], decimalPlaces: 1 } : { scale: 0, decimalPlaces: 4 };
      });
      data.push(r);
    });
    var baseName = opts.kind === "balance" ? "% of total assets" : "% of revenue";
    var unitText = show === "common" ? baseName : show === "yoy" ? "YoY %"
      : (ccy ? ccy + " " : "") + (unit.label || "units") + (ccy ? "" : " (reporting currency)");
    var cols = [{ field: "line", headerName: unitText, pinned: "left", cellDataType: "text" }].concat(periods.map(function (p, j) {
      return { field: "p" + j, headerName: p.label, headerTooltip: p.tip, cellDataType: "number", paren: true,
        scale: unit.scale === 1 ? 0 : unit.scale, scaleUnit: "", decimalPlaces: unit.dp };
    }));
    // the period switch: Annual / Quarterly (and TTM where the source has it), from the card's own period options
    var bar = el("div", { class: "stmt-bar" });
    var optsList = (opts.periodOptions || []).map(function (o) { return o && typeof o === "object" ? String(o.value) : String(o); });
    var cur = String(opts.period || optsList[0] || "annual");
    [["annual", "Annual"], ["quarter", "Quarterly"], ["ttm", "TTM"]].forEach(function (pair) {
      var v = optsList.filter(function (o) { return o.indexOf(pair[0]) === 0; })[0];
      if (!v) return;
      bar.appendChild(el("button", { type: "button", class: "chip" + (v === cur ? " on" : ""), title: pair[1] + " figures",
        onclick: function () { if (v !== cur && opts.onPeriod) opts.onPeriod(v); } }, pair[1]));
    });
    // Values / common size / YoY: a view choice kept per card (viewsel), never a layout edit
    var views = [["values", "Values", "Figures as reported", true],
      ["common", baseName, canCommon ? "Each line as a percent of " + (opts.kind === "balance" ? "total assets" : "revenue") + " of the same period"
        : "No " + (opts.kind === "balance" ? "total assets" : "revenue (or net interest + non-interest income)") + " line in this source", canCommon],
      ["yoy", "YoY %", canYoy ? "Change against the same period one year earlier; n/m when the base is zero or negative or the sign flips"
        : "No period one year earlier in what the source sent", canYoy]];
    if (opts.kind === "cash") views = views.filter(function (o) { return o[0] !== "common"; });
    var vbar = el("span", { class: "stmt-views" });
    views.forEach(function (o) {
      var b = el("button", { type: "button", class: "chip" + (o[0] === show ? " on" : ""), title: o[2],
        onclick: function () { if (o[0] !== show && o[3] && opts.onShow) opts.onShow(o[0]); } }, o[1]);
      if (!o[3]) b.disabled = true;
      vbar.appendChild(b);
    });
    bar.appendChild(vbar);
    var tv = null;
    if (periods.length < allPeriods || opts.all) {
      bar.appendChild(el("button", { type: "button", class: "chip" + (opts.all ? " on" : ""), title: opts.all ? "Show the latest periods only" : "Show every period the source sent",
        onclick: function () {
          host.textContent = "";
          var keep = opts.target || tv;                    // the card keeps the same table object
          Object.assign(keep, T.statement(host, Object.assign({}, opts, { all: !opts.all, target: keep })));
        } }, opts.all ? "Latest " + cap : "All " + allPeriods + " periods"));
    }
    var kindName = { income: "Income statement", balance: "Balance sheet", cash: "Cash flow" }[opts.kind] || "Statement";
    var what = /^quarter/.test(cur) ? "quarterly" : /^ttm/i.test(cur) ? "trailing twelve months" : "annual";
    bar.appendChild(el("span", { class: "stmt-note" }, kindName + ", " + what + ", newest first · " + unitText +
      (show !== "values" ? "" : (lines.some(function (l) { return l.kind === "eps"; }) ? " · per-share figures in " + (ccy || "currency units") : "") + " · negatives in (brackets)")));
    host.appendChild(bar);
    tv = T.table(host, {
      data: data, colDefs: cols, curated: true, noSort: true, noTranspose: true, dense: true,
      onCount: function () { if (opts.onCount) opts.onCount(lines.length + " lines · " + periods.length + " periods"); }
    });
    tv.statement = true;
    tv.lines = lines;
    tv.periods = periods;
    tv.unit = unit;
    tv.show = show;
    tv.baseRows = periods.length && lines.length ? tv.baseRows : [];
    return tv;
  };
})(window.T);
