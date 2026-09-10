import { afterEach, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { signalTotals } from "@/lib/signal-totals";
import { SignalSummary } from "@/components/signal-summary";
import type { ConfirmedSignal, CurrentSignals, FormingSignal } from "@/lib/api-types";
const now = Date.parse("2026-09-10T04:40:00Z");
const confirmed = (change: Partial<ConfirmedSignal> = {}): ConfirmedSignal => ({signal_id:"id",instrument_id:"EUR_USD",mode:"MICRO",timeframe:"M30",direction:"BUY",source_bar_start:"2026-09-10T04:00:00Z",source_bar_end:"2026-09-10T04:30:00Z",formed_at:null,confirmed_at:"2026-09-10T04:30:00Z",visible_until:"2026-09-10T05:00:00Z",market_data_source:"MARKET_DATA_PLATFORM",strategy_version:"v1",source_provider:"IG_DEMO",created_at:"2026-09-10T04:30:00Z",...change});
const forming = (change: Partial<FormingSignal> = {}): FormingSignal => ({instrument:"EUR_USD",instrument_id:"EUR_USD",authority:"IG_DEMO",mode:"MICRO",timeframe:"M30",direction:"SELL",state:"FORMING",source_bar_start:"2026-09-10T04:30:00Z",source_bar_end:"2026-09-10T05:00:00Z",formed_at:"2026-09-10T04:40:00Z",source_as_of:"2026-09-10T04:40:00Z",confirmed_at:null,visible_until:null,market_data_source:"MARKET_DATA_PLATFORM",...change});
function data(cs: ConfirmedSignal[] = [], fs: FormingSignal[] = []): CurrentSignals {return {as_of:new Date(now).toISOString(),confirmed:cs,forming:fs,forming_candidates:[['MICRO','M30'],['MICRO','H1'],['MACRO','H1'],['MACRO','H4']].map(([mode,timeframe])=>({authority:"IG_DEMO",instrument_id:"EUR_USD",mode,timeframe,state:"READY",source_as_of:new Date(now).toISOString(),source_bar_end:"2026-09-10T05:00:00Z"}))};}
const count = (d: CurrentSignals | null, mode: "MICRO" | "MACRO" = "MICRO", t = now) => signalTotals(d,mode,["EUR_USD"],t);
afterEach(()=>vi.useRealTimers());
it("Micro counts M30 and H1, excluding Macro and H4",()=>{
 expect(count(data([confirmed(),confirmed({timeframe:"H1"}),confirmed({timeframe:"H4"}),confirmed({mode:"MACRO",timeframe:"H1"})])).confirmed).toBe(2);
});
it("Macro counts both timeframes for one pair and both directions separately",()=>{
 expect(count(data([confirmed({mode:"MACRO",timeframe:"H1"}),confirmed({mode:"MACRO",timeframe:"H4"}),confirmed({mode:"MACRO",timeframe:"H4",direction:"SELL"}),confirmed()]),"MACRO").confirmed).toBe(3);
});
it("deduplicates repeated identities and repaired versions",()=>{
 expect(count(data([confirmed(),confirmed({signal_id:"revision",strategy_version:"v2"})],[forming(),forming()]))).toMatchObject({confirmed:1,forming:1});
});
it("excludes expired, future, invalid and nonmonitored confirmations",()=>{
 expect(count(data([confirmed({visible_until:new Date(now).toISOString()}),confirmed({confirmed_at:"2026-09-10T04:45:00Z"}),confirmed({instrument_id:"GBP_USD"}),confirmed({direction:"NONE"}),confirmed({visible_until:"invalid"})])).confirmed).toBe(0);
});
it("counts forming independently of confirmed precedence in a cell",()=>{
 expect(count(data([confirmed()],[forming(),forming({timeframe:"H1"})]))).toMatchObject({confirmed:1,forming:2});
});
it("counts Macro forming H1/H4 without Micro",()=>{
 expect(count(data([],[forming(),forming({mode:"MACRO",timeframe:"H1"}),forming({mode:"MACRO",timeframe:"H4"})]),"MACRO").forming).toBe(2);
});
it("expires forming and rejects stale/future sources",()=>{
 expect(count(data([],[forming({source_bar_end:new Date(now).toISOString()}),forming({source_as_of:"2026-09-10T04:25:00Z"}),forming({source_as_of:"2026-09-10T04:45:00Z"})])).forming).toBe(0);
});
it("shows unavailable totals on loading, failed or delayed refresh",()=>{
 expect(count(null).confirmed).toBeNull();expect(count({...data(),as_of:"2026-09-10T04:39:00Z"}).confirmed).toBeNull();expect(signalTotals(data(),"MICRO",["EUR_USD"],now,true).forming).toBeNull();
});
it("reports partial coverage and never invents zero for missing inputs",()=>{
 const d=data([],[forming()]);d.forming_candidates![1].state="STALE";expect(count(d)).toMatchObject({forming:1,formingNote:"Available data · 1/2 evaluations ready"});d.forming_candidates![0].state="WAITING_FOR_DATA";expect(count(d).forming).toBeNull();
});
it("ignores wrong-authority forming matches",()=>{expect(count(data([],[forming({authority:"IG_LIVE"})])).forming).toBe(0);});
it("renders existing card classes and separate mode totals",()=>{
 vi.useFakeTimers();vi.setSystemTime(now);const d=data([confirmed(),confirmed({mode:"MACRO",timeframe:"H1"}),confirmed({mode:"MACRO",timeframe:"H4"})],[forming()]);
 const micro=renderToStaticMarkup(<SignalSummary data={d} mode="MICRO" instrumentIds={['EUR_USD']}/>);
 const macro=renderToStaticMarkup(<SignalSummary data={d} mode="MACRO" instrumentIds={['EUR_USD']}/>);
 expect(micro).toContain('<b>1</b><small>Confirmed Signals</small>');expect(micro).toContain('<b>1</b><small>Forming Signals</small>');expect(macro).toContain('<b>2</b><small>Confirmed Signals</small>');expect(macro).toContain('Macro · H1 + H4');expect(micro).toContain('class="kpi"');
});
