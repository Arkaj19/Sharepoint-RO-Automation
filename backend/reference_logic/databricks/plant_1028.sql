-------------------------------------1028-----------------------------------------

%sql
CREATE OR REPLACE VIEW con_dev.s4_mdm.mm_S_MARC
AS
WITH base AS (
    SELECT distinct * from (
        SELECT
        marc.matnr,
        marc.werks,MARC.BESKZ,
        case when MARC.SFCPF = 'DAP3' then 'US31'
    -- when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARA.MTART in ('ROH','VERP','LEIH') then 'US31'
    when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARC.LGFSB in ('DWHS','KWHS','CSA','SAPL')  then 'US31'
        when MARC.BESKZ = 'F' and MARC.SOBSL IS NOT NULL then 'US31'
        -- when MARC.BESKZ = 'F' then 'US31'
        when MARC.BESKZ in ('E','X') then 'US31'
        when MARC.BESKZ is null then 'US31'
        else marc.werks
        end as derived_werks
    FROM con_dev.silver_sap.marc_dap MARC
    INNER JOIN con_dev.silver_sap.mara_dap MARA
        ON MARC.MATNR = MARA.MATNR
        AND MARA.LVORM IS NULL
    AND MARA.MTART <> 'NVAL'
     where MARC.LVORM IS NULL and MARC.WERKS = '1028'

     UNION ALL

      SELECT
        marc.matnr,
        marc.werks,MARC.BESKZ,
        case when MARC.SFCPF = 'DAP3' then 'US31'
    -- when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARA.MTART in ('ROH','VERP','LEIH') then 'US28'
    when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARC.LGFSB in ('DWHS','KWHS','CSA','SAPL') then 'US28'
        when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and (MARC.LGFSB not in ('DWHS','KWHS','CSA','SAPL') or MARC.LGFSB IS NULL) then 'US28'
        when MARC.BESKZ = 'F' and MARC.SOBSL IS NOT NULL then 'US31'
        -- when MARC.BESKZ = 'F' then 'US31'
        when MARC.BESKZ in ('E','X') then 'US28'
        when MARC.BESKZ is null then 'US28'
        else marc.werks
        end as derived_werks
    FROM con_dev.silver_sap.marc_dap MARC

     INNER JOIN con_dev.silver_sap.mara_dap MARA
        ON MARC.MATNR = MARA.MATNR
        AND MARA.LVORM IS NULL
    AND MARA.MTART <> 'NVAL'
     where MARC.LVORM IS NULL and MARC.WERKS = '1028')
),

-- Apply LGPRO / LGFSB override
mapped AS (
    SELECT distinct
        `MARC`.`MATNR` as PRODUCT,REGEXP_REPLACE(MARA_NEW.MATNR_new, '^0+', '') as MATNR_new,
    -- case when MARA_NEW.MATNR_new is not null then REGEXP_REPLACE(MARA_NEW.MATNR_new, '^0+', '') else `MARC`.`MATNR` end as PRODUCT,
        base.derived_werks as werks,
    case when MARC.BESKZ = 'X' then 'ND' else `MARC`.`DISMM` end as DISMM,
    `MARC`.`DISPO`,
    case when MARA.MTART = 'LEIH' then '02'
    when base.DERIVED_WERKS in ('US30','US31','US33') then 'SP' else '02' end as MTVFP,
    case when `MARC`.`MMSTA`='01' then 'PW'
    when `MARC`.`MMSTA`='BP' then 'BP'
    when `MARC`.`MMSTA`='ZP' then 'ZP'
    when `MARC`.`MMSTA`='02' then 'TB'
    when `MARC`.`MMSTA`='BR' then 'BR'
    else `MARC`.`MMSTA` end as MMSTA,
    to_date(`MARC`.`MMSTD`) as MMSTD,
    'CG01' as KOKRS,
    base.derived_werks as PRCTR,
    case when base.derived_werks in ('US30','US31','US33','CA02') then ''
    when MARA.MTART = 'FERT' and MARC.BESKZ = 'E' then 'CS' else `MARC`.`AUSME` end as AUSME,
    `MARC`.`XCHPF`,
    `MARC`.`SERNP`,
    `MARC`.`XMCNG`,
    `MARC`.`EPRIO`,
    `MARC`.`LADGR`,
    `MARC`.`KZKUP`,
    `MARC`.`KZECH`,
    `MARC`.`KZKRI`,
    `MARC`.`QMATA`,
    `MARC`.`KZDKZ`,
    `MARC`.`PRFRQ`,
    `MARC`.`SSQSS`,
    `MARC`.`QZGTP`,
    `MARC`.`QSSYS`,
    `MARC`.`MTVER`,
    `MARC`.`HERKL`,
    `MARC`.`HERKR`,
    `MARC`.`STEUC`,
    `MARC`.`INDUS`,
    case when ekgrp_upd.EKGRP is not null then ekgrp_upd.EKGRP else '' end as EKGRP,
    `MARC`.`INSMK`,
    `MARC`.`KORDB`,
    `MARC`.`KAUTB`,
    '' as TAXIM,
    `MARC`.`MAABC`,
    `MARC`.`DISGR`,
    case when MARA.MTART = 'FERT' then '40' else '' end as `STRGR`,
    `MARC`.`MINBE`,
    case when MARA.MTART = 'FERT' then '14'
    when MARC.DISMM = 'P1' then '14' else '' end as `FXHOR`,
    `MARC`.`LFRHY`,
    case when MARA.MTART = 'FERT' then '2' else '' end as `VRMOD`,
    case when MARA.MTART = 'FERT' then '30' else '' end as `VINT1`,
    case when MARA.MTART = 'FERT' then '7' else '' end as `VINT2`,
    `MARC`.`MISKZ`,
    '' as PRGRP,
    '' as PRWRK,
    '' as UMREF,
    `MARC`.`DISLS`,
    -- case when MARA.MTART = 'FERT' then (`MARC`.`BSTMI` * MARM_EA.UMREN) else `MARC`.`BSTMI` end as BSTMI,
    -- case when MARA.MTART = 'FERT' then (`MARC`.`BSTMA` * MARM_EA.UMREN) else `MARC`.`BSTMA` end as BSTMA,
    -- case when MARA.MTART = 'FERT' then (`MARC`.`BSTFE` * MARM_EA.UMREN) else `MARC`.`BSTFE` end as BSTFE,
    case when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') and (MARC.SFCPF = 'DAP3' or MARC.SOBSL = '30') then (`MARC`.`BSTMI` * MARM_EA.UMREN)
    when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') then '0'
    when MARA.MEINS = 'CS' then (`MARC`.`BSTMI` * MARM_EA.UMREN)
    else `MARC`.`BSTMI` end as BSTMI,
    case when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') and (MARC.SFCPF = 'DAP3' or MARC.SOBSL = '30') then (`MARC`.`BSTMA` * MARM_EA.UMREN)
    when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') then '0'
    when MARA.MEINS = 'CS' then (`MARC`.`BSTMA` * MARM_EA.UMREN)
    else `MARC`.`BSTMA` end as BSTMA,
    case when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') and (MARC.SFCPF = 'DAP3' or MARC.SOBSL = '30') then (`MARC`.`BSTFE` * MARM_EA.UMREN)
    when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') then '0'
    when MARA.MEINS = 'CS' then (`MARC`.`BSTFE` * MARM_EA.UMREN)
    else `MARC`.`BSTFE` end as BSTFE,

    `MARC`.`LAGPR`,
    `MARC`.`LOSFX`,
    case when `MARC`.`LOSFX` = 0.00 then '' else 'USD' end as WAERS,
    `MARC`.`AUSSS`,
    `MARC`.`MABST`,
    -- case when MARA.MTART = 'FERT' then (`MARC`.`BSTRF` * MARM_EA.UMREN) else `MARC`.`BSTRF` end as`BSTRF`,
    case when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') and (MARC.SFCPF = 'DAP3' or MARC.SOBSL = '30') then (`MARC`.`BSTRF` * MARM_EA.UMREN)
    when MARA.MTART in ('FERT', 'HALB') and base.derived_werks in ('US30','US31','US33','CA02') then '0'
    when MARA.MEINS = 'CS' then (`MARC`.`BSTRF` * MARM_EA.UMREN)
    else `MARC`.`BSTRF` end as BSTRF,
    `MARC`.`TAKZT`,
    `MARC`.`RDPRF`,
    `MARC`.`MEGRU`,
    `MARC`.`EISBE`,
    `MARC`.`EISLO`,
    `MARC`.`SHZET`,
    `MARC`.`LGRAD`,
    `MARC`.`RWPRO`,
    `MARC`.`SHFLG`,
    `MARC`.`SHPRO`,
    `MARC`.`AHDIS`,
    '' as SFTY_STK_METH,
    `MARC`.`SBDKZ`,
    `MARC`.`KAUSF`,
    `MARC`.`KZBED`,
    `MARC`.`KZAUS`,
    to_date(`MARC`.`AUSDT`) as AUSDT,
    REGEXP_REPLACE(MARC.NFMAT, '^0+', '') as NFMAT,
    `MARC`.`SAUFT`,
    `MARC`.`SFEPR`,
    case when MARC.BESKZ = 'E' and base.derived_werks = 'US31' then 'F' else `MARC`.`BESKZ` end as BESKZ,

    case when base.derived_werks = 'US31' and MARC.BESKZ = 'F' and MARC.SOBSL = '40' then '64'
    when base.derived_werks = 'US31' and MARC.BESKZ = 'F' and MARC.SOBSL = '41' then '63'
    when base.derived_werks = 'US31' and MARC.BESKZ = 'F' and MARC.SOBSL = '42' then '6A'
    -- when base.derived_werks = 'US28' and MARC.BESKZ = 'F' and MARC.SOBSL in ('40','41','42') then ''
        when base.derived_werks = 'US31' and MARC.BESKZ = 'E' then '66'
    when base.derived_werks = 'US31' and MARC.BESKZ = 'F' and MARC.SOBSL = '' then '66'
    else MARC.SOBSL end as SOBSL,
    -- `MARC`.`LGPRO`,
    case when base.derived_werks = 'US31' then ''
        when base.derived_werks = 'US28' and MARA.MTART = 'FERT' then 'PL01'
    when base.derived_werks = 'US28' and MARA.MTART = 'HALB' then 'PWIP'
        when base.derived_werks = 'US28' and MARC.LGPRO in ('DWHS','KWHS','CSA') then ''
         else `MARC`.`LGPRO` end as LGPRO,
    `MARC`.`WZEIT`,
    `MARC`.`KZPSP`,
    -- `MARC`.`LGPRO`,
    case when base.derived_werks = 'US31' and MARC.LGFSB in ('DWHS','KWHS') then 'DWHS'
        when base.derived_werks = 'US31' and MARC.LGFSB in ('CSA') then 'CSA'
        when base.derived_werks = 'US31' then 'DWHS'
        when base.derived_werks = 'US28' then ''
         else `MARC`.`LGPRO` end as LGFSB,
    `MARC`.`MRPPP`,
    `MARC`.`DZEIT`,
    `MARC`.`PLIFZ`,
    `MARC`.`WEBAZ`,
    `MARC`.`RGEKZ`,
    `MARC`.`VSPVB`,
    `MARC`.`FABKZ`,
    `MARC`.`SCHGT`,
    `MARC`.`FHORI`,
    '' as SCM_RRP_TYPE,
    '' as SCM_HEUR_ID,
    '' as SCM_RRP_SEL_GROUP,
    '' as SCM_PACKAGE_ID,
    '' as SCM_LSUOM,
    '' as SCM_TARGET_DUR,
    '' as SCM_REORD_DUR,
    '' as SCM_TSTRID,
    '' as SCM_GRPRT,
    '' as SCM_CONHAP,
    '' as SCM_HUNIT,
    '' as SCM_GIPRT,
    '' as SCM_CONHAP_OUT,
    '' as SCM_HUNIT_OUT,
    case when MARA.MTART = 'FERT' then 'W' else '' end as `PERKZ`,
    `MARC`.`PERIV`,
    `MARC`.`AUFTL`,
    `MARC`.`VRBWK`,
    `MARC`.`VRBMT`,
    `MARC`.`VRBDT`,
    `MARC`.`VRBFK`,
    `MARC`.`AUTRU`,
    `MARC`.`KZKFK`,
    `MARC`.`BASMG`,
    `MARC`.`UEETO`,
    `MARC`.`UNETO`,
    `MARC`.`UEETK`,
    case when base.derived_werks in ('US30','US31','US33','CA02') then ''
    when MARA.MTART = 'FERT' and MARC.BESKZ = 'E' then 'CS' else `MARC`.`FRTME` end as FRTME,
    case when base.derived_werks in ('US30','US31','US33','CA02') then ''
    when MARC.SFCPF = 'DAP1' then '001'
    when MARC.SFCPF = 'DAP2' then '002'
    when MARC.SFCPF = 'DAP3' then '003'
     else `MARC`.`FEVOR` end as FEVOR,
    case when base.derived_werks in ('US30','US31','US33','CA02') and MARC.SFCPF <> 'DAP3' then ''
    when MARC.SOBSL = '30' then ''
    else `MARC`.`SFCPF` end as SFCPF,
    `MARC`.`RUEZT`,
    `MARC`.`TRANZ`,
    `MARC`.`BEARZ`,
    `MARC`.`ABCIN`,
    `MARC`.`CCFIX`,
    `MARC`.`NCOST`,
    case when cast(`MARC`.`LOSGR` as string) = '0.000' then ''
    when `MARA`.`MTART` in ('FERT','HALB') then '000001'
    when `MARC`.`AWSLS` = 'Z00001' then '000001'
    else `MARC`.`AWSLS` end as AWSLS,
    -- case when MARC.BSTMI <> '0.000' and MARA.MTART = 'FERT' then (`MARC`.`BSTMI` * MARM_EA.UMREN)
        -- when MARC.BSTMA <> '0.000' and MARA.MTART = 'FERT' then (`MARC`.`BSTMA` * MARM_EA.UMREN)
        -- when MARC.BSTFE <> '0.000' and MARA.MTART = 'FERT' then (`MARC`.`BSTFE` * MARM_EA.UMREN)
        -- else `MARC`.`LOSGR` end as LOSGR,
    case when `MARC`.`LOSGR` = 0.000 then ''
    when MARA.MTART = 'FERT' and MARA.MEINS = 'CS' then cast(`MARC`.`LOSGR` * MARM_EA.UMREN as string) else cast(`MARC`.`LOSGR` as string) end as LOSGR,
    `MARC`.`MAXLZ`,
    `MARC`.`LZEIH`,
    `MARC`.`VRVEZ`,
    `MARC`.`VBEAZ`,
    `MARC`.`VBAMG`,
    `MARC`.`FPRFM`,
    `MARC`.`BWSCL`,
    `MARC`.`CONS_PROCG`,
    `MARC`.`MULTIPLE_EKGRP`,
    `MARC`.`GI_PR_TIME`,
    `MARC`.`SERVG`,
    '' as PSTATL,
    '' as PSTATA,
    '' as PSTATE,
    '' as PSTATQ,
    '' as PSTATV
    FROM base
  left join con_dev.silver_sap.marc_dap MARC on MARC.matnr = base.matnr
  INNER JOIN con_dev.s4_mdm.mm_S_MARA S_MARA
  ON MARC.MATNR = S_MARA.PRODUCT
  left join con_dev.silver_sap.marm_dap MARM
on MARC.MATNR=MARM.MATNR
LEFT JOIN con_dev.silver_sap.marm_dap MARM_EA
    ON MARM.MATNR = MARM_EA.MATNR
    AND MARM_EA.MEINH = 'EA'
left join con_dev.silver_sap.mara_dap MARA
on MARC.MATNR=MARA.MATNR
AND MARA.MTART <> 'NVAL'
left join con_dev.s4_mdm.ekgrp_updated ekgrp_upd
on REGEXP_REPLACE(MARC.MATNR, '^0+', '') = ekgrp_upd.MATNR
AND MARC.WERKS = ekgrp_upd.WERKS
AND ekgrp_upd.WERKS = '1028'
left join con_dev.s4_mdm.1025_new_matnr MARA_NEW
on MARC.MATNR=MARA_NEW.MATNR
  WHERE MARA.LVORM is null and MARC.LVORM is null and MARC.WERKS = '1028' and base.DERIVED_WERKS <> '1028'
)

-- Final result
SELECT * FROM mapped


----------------------------------------------------------------------------------------------------------

%sql
CREATE OR REPLACE VIEW con_dev.s4_mdm.mm_S_MBEW
AS

WITH base AS (
    SELECT distinct * from (
        SELECT
        marc.matnr,
        marc.werks,MARC.BESKZ,
        case when MARC.SFCPF = 'DAP3' then 'US31'
    -- when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARA.MTART in ('ROH','VERP','LEIH') then 'US31'
    when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARC.LGFSB in ('DWHS','KWHS','CSA','SAPL')  then 'US31'
        when MARC.BESKZ = 'F' and MARC.SOBSL IS NOT NULL then 'US31'
        -- when MARC.BESKZ = 'F' then 'US31'
        when MARC.BESKZ in ('E','X') then 'US31'
        when MARC.BESKZ is null then 'US31'
        else marc.werks
        end as derived_werks
    FROM con_dev.silver_sap.marc_dap MARC
    INNER JOIN con_dev.silver_sap.mara_dap MARA
        ON MARC.MATNR = MARA.MATNR
        AND MARA.LVORM IS NULL
    AND MARA.MTART <> 'NVAL'
     where MARC.LVORM IS NULL and MARC.WERKS = '1028'

     UNION ALL

      SELECT
        marc.matnr,
        marc.werks,MARC.BESKZ,
        case when MARC.SFCPF = 'DAP3' then 'US31'
    -- when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARA.MTART in ('ROH','VERP','LEIH') then 'US28'
    when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and MARC.LGFSB in ('DWHS','KWHS','CSA','SAPL') then 'US28'
        when MARC.BESKZ = 'F' and MARC.SOBSL IS NULL and (MARC.LGFSB not in ('DWHS','KWHS','CSA','SAPL') or MARC.LGFSB IS NULL) then 'US28'
        when MARC.BESKZ = 'F' and MARC.SOBSL IS NOT NULL then 'US31'
        -- when MARC.BESKZ = 'F' then 'US31'
        when MARC.BESKZ in ('E','X') then 'US28'
        when MARC.BESKZ is null then 'US28'
        else marc.werks
        end as derived_werks
    FROM con_dev.silver_sap.marc_dap MARC

     INNER JOIN con_dev.silver_sap.mara_dap MARA
        ON MARC.MATNR = MARA.MATNR
        AND MARA.LVORM IS NULL
    AND MARA.MTART <> 'NVAL'
     where MARC.LVORM IS NULL and MARC.WERKS = '1028')
),

-- Apply LGPRO / LGFSB override
mapped AS (
    SELECT distinct
        `MBEW`.`MATNR` as PRODUCT,REGEXP_REPLACE(MARA_NEW.MATNR_new, '^0+', '') as MATNR_new,
        -- case when MARA_NEW.MATNR_new is not null then REGEXP_REPLACE(MARA_NEW.MATNR_new, '^0+', '') else `MBEW`.`MATNR` end as PRODUCT,
        base.derived_werks as BWKEY,
    case when `MBEW`.`BWTAR` = '~' then '' else `MBEW`.`BWTAR` end as BWTAR,
`MBEW`.`BWTTY`,
`MBEW`.`MLAST`,
case when `MARA`.`PRDHA` in ('009990099900000999','00999') then 'RACK'
else `MARA`.`MTART` end as BKLAS,
case when MARA.MTART = 'FERT' then 'YSIT' else NULL end as EKLAS,
`MBEW`.`QKLAS`,
`MBEW`.`VPRSV`,
'USD' as WAERS,
'' as `VERPR`,
case when MARA.MTART = 'FERT' and MARA.MEINS = 'CS' then round(((`MBEW`.`STPRS` * `MBEW`.`PEINH`) / (MARM_EA.UMREN * `MBEW`.`PEINH`)),2) else `MBEW`.`STPRS` end as STPRS,
`MBEW`.`PEINH`,
`MBEW`.`ZKPRS`,
`MBEW`.`ZKDAT`,
case when MARA.MTART not in ('FERT','HALB') then cast(`MBEW`.`STPRS` as string) else '' end as `ZPLP1`,
-- cast(current_date as string) as ZPLD1,
case when MARA.MTART not in ('FERT','HALB') then cast(`MBEW`.`STPRS` as string) else '' end as ZPLD1,
`MBEW`.`ZPLP2`,
to_date(`MBEW`.`ZPLD2`,'MM/dd/yyyy') as ZPLD2,
`MBEW`.`ZPLP3`,
to_date(`MBEW`.`ZPLD3`,'MM/dd/yyyy') as ZPLD3,
`MBEW`.`BWPRS`,
`MBEW`.`BWPS1`,
`MBEW`.`VJBWS`,
`MBEW`.`BWPEI`,
`MBEW`.`BWPRH`,
`MBEW`.`BWPH1`,
`MBEW`.`VJBWH`,
`MBEW`.`XLIFO`,
`MBEW`.`MYPOL`,
`MBEW`.`ABWKZ`,
`MBEW`.`MTUSE`,
`MBEW`.`MTORG`,
'' as OWPNR,
'X' as `HKMAT`,
'X' as `EKALR`,
`MBEW`.`HRKFT`,
case when base.derived_werks in ('CA02','US30','US31','US33') then '' else `MBEW`.`KOSGR` end as KOSGR,
`MBEW`.`BWSPA`,
'' as PSTATB,
'' as PSTATG
    FROM base
  left join con_dev.silver_sap.mbew_dap MBEW on MBEW.matnr = base.matnr
     left join con_dev.silver_sap.mara_dap MARA on MARA.matnr = base.matnr AND MARA.MTART <> 'NVAL'
  INNER JOIN con_dev.s4_mdm.mm_S_MARA S_MARA
  ON MBEW.MATNR = S_MARA.PRODUCT
  left join con_dev.silver_sap.marm_dap MARM
on MBEW.MATNR=MARM.MATNR
LEFT JOIN con_dev.silver_sap.marm_dap MARM_EA
    ON MARM.MATNR = MARM_EA.MATNR
    AND MARM_EA.MEINH = 'EA'
left join con_dev.s4_mdm.1025_new_matnr MARA_NEW
on MBEW.MATNR=MARA_NEW.MATNR
  WHERE `MBEW`.`LVORM` IS NULL and MARA.LVORM IS NULL and MBEW.matnr in (select distinct matnr from con_dev.silver_sap.mara_dap where mtart <> 'UNBW' and LVORM IS NULL) and base.DERIVED_WERKS <> '1028' and MBEW.bwkey = '1028'
)

-- Final result
SELECT * FROM mapped
