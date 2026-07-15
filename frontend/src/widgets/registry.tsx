import type { ComponentType } from 'react';
import type { WidgetConfig, WidgetType } from '../types';
import { CanMessageDisplay, TextDisplay } from './displays';
import { ButtonWidget, CheckboxWidget, DropdownWidget, SliderWidget } from './controls';
import {
  MultiButtonWidget,
  MultiCheckboxWidget,
  MultiDropdownWidget,
  MultiSliderWidget,
  FunctionMultiButtonWidget,
  RandomMultiButtonWidget,
} from './MultiControls';
import { TxBox } from './TxBox';
import { ReplayBox } from './ReplayBox';
import { IsoTpBox } from './IsoTpBox';
import { GraphWidget } from './GraphWidget';
import { TestRunnerBox } from './TestRunnerBox';
import { FunctionButtonWidget } from './FunctionButtonWidget';
import { RandomButtonWidget } from './RandomButtonWidget';

interface WidgetMeta {
  label: string;
  component: ComponentType<{ config: WidgetConfig }>;
  defaultSize: { w: number; h: number; minW: number; minH: number };
}

export const WIDGET_REGISTRY: Record<WidgetType, WidgetMeta> = {
  button: {
    label: '버튼',
    component: ButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  checkbox: {
    label: '체크박스',
    component: CheckboxWidget,
    defaultSize: { w: 2, h: 1, minW: 1, minH: 1 },
  },
  dropdown: {
    label: '드롭다운',
    component: DropdownWidget,
    defaultSize: { w: 3, h: 1, minW: 2, minH: 1 },
  },
  slider: {
    label: '슬라이더',
    component: SliderWidget,
    defaultSize: { w: 4, h: 2, minW: 2, minH: 1 },
  },
  functionButton: {
    label: 'Function 버튼',
    component: FunctionButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  randomButton: {
    label: 'Random 버튼',
    component: RandomButtonWidget,
    defaultSize: { w: 2, h: 2, minW: 1, minH: 1 },
  },
  multiButton: {
    label: '멀티 버튼',
    component: MultiButtonWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  multiCheckbox: {
    label: '멀티 체크박스',
    component: MultiCheckboxWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  multiDropdown: {
    label: '멀티 드롭다운',
    component: MultiDropdownWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  multiSlider: {
    label: '멀티 슬라이더',
    component: MultiSliderWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  functionMultiButton: {
    label: '멀티 Function 버튼',
    component: FunctionMultiButtonWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  randomMultiButton: {
    label: '멀티 Random 버튼',
    component: RandomMultiButtonWidget,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  txBox: {
    label: 'CAN 메시지 전송[IG]',
    component: TxBox,
    defaultSize: { w: 7, h: 5, minW: 4, minH: 3 },
  },
  isotpTx: {
    label: 'ISO-TP 메시지 전송[UDS]',
    component: IsoTpBox,
    defaultSize: { w: 6, h: 4, minW: 1, minH: 2 },
  },
  replayBox: {
    label: 'CAN 로그 Replay',
    component: ReplayBox,
    defaultSize: { w: 5, h: 3, minW: 3, minH: 2 },
  },
  testRunner: {
    label: '테스트 Sequence 실행기',
    component: TestRunnerBox,
    defaultSize: { w: 8, h: 6, minW: 4, minH: 3 },
  },
  signalGraph: {
    label: 'CAN 신호 그래프',
    component: GraphWidget,
    defaultSize: { w: 7, h: 5, minW: 1, minH: 2 },
  },
  canMessageDisplay: {
    label: 'CAN 메시지 표시창',
    component: CanMessageDisplay,
    defaultSize: { w: 6, h: 5, minW: 3, minH: 2 },
  },
  textDisplay: {
    label: '텍스트 표시창',
    component: TextDisplay,
    defaultSize: { w: 3, h: 2, minW: 2, minH: 1 },
  },

};
