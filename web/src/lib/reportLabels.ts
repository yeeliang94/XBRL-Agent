// Short operator labels for SSM report fields. The full taxonomy label stays
// available as hover text so the mTool destination remains identifiable.

const ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth"];

const LABELS: Record<string, string> = {
  ssmt_DisclosureOfDirectorsReportExplanatory: "Report text",
  ssmt_NumberOfDirectorsSigningDirectorsReport: "Directors signing",
  ssmt_DisclosureOfStatusOfDividend: "Final dividend",
  ssmt_DisclosureOfContingentOrOtherLiabilityBeingEnforceableWithinTwelveMonthsAfterEndOfFinancialYear:
    "Liabilities enforceable within 12 months",
  ssmt_DisclosureOfOccurenceOfAnySubstantialMaterialOrUnusualInNatureItemsTransactionsOrEvents:
    "Material or unusual items",
  ssmt_DisclosureOfDirectorsReceivedOrBecomeEntitledToReceiveOtherBenefitsByReasonOfContractMadeByCompanyOrRelatedCorporation:
    "Directors’ benefits from contracts",
  ssmt_DateOfSigningDirectorsReport: "Date signed",
  ssmt_DisclosureOfAuditorsReportToMembersExplanatory: "Report text",
  ssmt_TypeOfAuditorsOpinion: "Audit opinion",
  ssmt_DateOfSigningAuditorsReport: "Date signed",
  ssmt_LicenseNumberOfAuditor: "Auditor licence number",
  ssmt_NameOfAuditorSigningReport: "Signing auditor",
  ssmt_RegistrationNumberOfAuditFirm: "Audit firm registration number",
  ssmt_NameOfAuditFirm: "Audit firm",
  ssmt_AddressOne: "Address line 1",
  ssmt_AddressTwo: "Address line 2",
  ssmt_AddressThree: "Address line 3",
  ssmt_PostcodeOfAuditFirm: "Postcode",
  ssmt_TownWhereAuditFirmIsLocated: "Town",
  ssmt_StateWhereAuditFirmIsLocated: "State",
};

const DIRECTOR_FIELD = /^ssmt_(Name|TypeOfIdentification|IdentificationNumber)Of(?:the)?(First|Second|Third|Fourth|Fifth)DirectorWhoSignedDirectorsReport$/;
const DIRECTOR_PART: Record<string, string> = { Name: "name", TypeOfIdentification: "ID type", IdentificationNumber: "ID number" };

/** 1-based signing-director slot for a director field, else null. */
export function directorSlot(concept: string): number | null {
  const match = DIRECTOR_FIELD.exec(concept);
  return match ? ORDINALS.indexOf(match[2]) + 1 : null;
}

export function reportFieldLabel(concept: string, fallback: string): string {
  const match = DIRECTOR_FIELD.exec(concept);
  if (match) return `Director ${ORDINALS.indexOf(match[2]) + 1} ${DIRECTOR_PART[match[1]]}`;
  return LABELS[concept] ?? fallback;
}

export const REPORT_SHEET_LABELS: Record<string, string> = {
  "Report-Directors": "Directors’ report",
  "Report-Auditors": "Auditors’ report",
};
