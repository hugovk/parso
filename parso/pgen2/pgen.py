# Copyright 2004-2005 Elemental Security, Inc. All Rights Reserved.
# Licensed to PSF under a Contributor Agreement.

# Modifications:
# Copyright David Halter and Contributors
# Modifications are dual-licensed: MIT and PSF.

"""
Specifying grammars in pgen is possible with this grammar::

    grammar: (NEWLINE | rule)* ENDMARKER
    rule: NAME ':' rhs NEWLINE
    rhs: items ('|' items)*
    items: item+
    item: '[' rhs ']' | atom ['+' | '*']
    atom: '(' rhs ')' | NAME | STRING

This grammar is self-referencing.
"""

from ast import literal_eval

from parso.pgen2.grammar import Grammar, DFAPlan
from parso.pgen2.grammar_parser import GrammarParser, NFAState


class DFAState(object):
    def __init__(self, from_rule, nfa_set, final):
        assert isinstance(nfa_set, set)
        assert isinstance(next(iter(nfa_set)), NFAState)
        assert isinstance(final, NFAState)
        self.from_rule = from_rule
        self.nfa_set = nfa_set
        self.is_final = final in nfa_set
        self.arcs = {}  # map from terminals/nonterminals to DFAState
        self.ilabel_to_plan = {}
        self.nonterminal_arcs = {}

    def add_arc(self, next_, label):
        assert isinstance(label, str)
        assert label not in self.arcs
        assert isinstance(next_, DFAState)
        self.arcs[label] = next_

    def unifystate(self, old, new):
        for label, next_ in self.arcs.items():
            if next_ is old:
                self.arcs[label] = new

    def __eq__(self, other):
        # Equality test -- ignore the nfa_set instance variable
        assert isinstance(other, DFAState)
        if self.is_final != other.is_final:
            return False
        # Can't just return self.arcs == other.arcs, because that
        # would invoke this method recursively, with cycles...
        if len(self.arcs) != len(other.arcs):
            return False
        for label, next_ in self.arcs.items():
            if next_ is not other.arcs.get(label):
                return False
        return True

    __hash__ = None  # For Py3 compatibility.

    def __repr__(self):
        return '<%s: %s is_final=%s>' % (
            self.__class__.__name__, self.from_rule, self.is_final
        )


class ReservedString(object):
    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__, self.value)


def _simplify_dfas(dfas):
    # This is not theoretically optimal, but works well enough.
    # Algorithm: repeatedly look for two states that have the same
    # set of arcs (same labels pointing to the same nodes) and
    # unify them, until things stop changing.

    # dfas is a list of DFAState instances
    changes = True
    while changes:
        changes = False
        for i, state_i in enumerate(dfas):
            for j in range(i + 1, len(dfas)):
                state_j = dfas[j]
                if state_i == state_j:
                    #print "  unify", i, j
                    del dfas[j]
                    for state in dfas:
                        state.unifystate(state_j, state_i)
                    changes = True
                    break


def _make_dfas(start, finish):
    """
    This is basically doing what the powerset construction algorithm is doing.
    """
    # To turn an NFA into a DFA, we define the states of the DFA
    # to correspond to *sets* of states of the NFA.  Then do some
    # state reduction.
    assert isinstance(start, NFAState)
    assert isinstance(finish, NFAState)

    def addclosure(nfa_state, base_nfa_set):
        assert isinstance(nfa_state, NFAState)
        if nfa_state in base_nfa_set:
            return
        base_nfa_set.add(nfa_state)
        for nfa_arc in nfa_state.arcs:
            if nfa_arc.nonterminal_or_string is None:
                addclosure(nfa_arc.next, base_nfa_set)

    base_nfa_set = set()
    addclosure(start, base_nfa_set)
    states = [DFAState(start.from_rule, base_nfa_set, finish)]
    for state in states:  # NB states grows while we're iterating
        arcs = {}
        # Find state transitions and store them in arcs.
        for nfa_state in state.nfa_set:
            for nfa_arc in nfa_state.arcs:
                if nfa_arc.nonterminal_or_string is not None:
                    nfa_set = arcs.setdefault(nfa_arc.nonterminal_or_string, set())
                    addclosure(nfa_arc.next, nfa_set)

        # Now create the dfa's with no None's in arcs anymore. All Nones have
        # been eliminated and state transitions (arcs) are properly defined, we
        # just need to create the dfa's.
        for nonterminal_or_string, nfa_set in arcs.items():
            for nested_state in states:
                if nested_state.nfa_set == nfa_set:
                    # The DFA state already exists for this rule.
                    break
            else:
                nested_state = DFAState(start.from_rule, nfa_set, finish)
                states.append(nested_state)

            state.add_arc(nested_state, nonterminal_or_string)
    return states  # List of DFAState instances; first one is start


def _dump_nfa(start, finish):
    print("Dump of NFA for", start.from_rule)
    todo = [start]
    for i, state in enumerate(todo):
        print("  State", i, state is finish and "(final)" or "")
        for label, next_ in state.arcs:
            if next_ in todo:
                j = todo.index(next_)
            else:
                j = len(todo)
                todo.append(next_)
            if label is None:
                print("    -> %d" % j)
            else:
                print("    %s -> %d" % (label, j))


def _dump_dfas(dfas):
    print("Dump of DFA for", dfas[0].from_rule)
    for i, state in enumerate(dfas):
        print("  State", i, state.is_final and "(final)" or "")
        for nonterminal, next_ in state.arcs.items():
            print("    %s -> %d" % (nonterminal, dfas.index(next_)))


def generate_grammar(bnf_grammar, token_namespace):
    """
    ``bnf_text`` is a grammar in extended BNF (using * for repetition, + for
    at-least-once repetition, [] for optional parts, | for alternatives and ()
    for grouping).

    It's not EBNF according to ISO/IEC 14977. It's a dialect Python uses in its
    own parser.
    """
    rule_to_dfas = {}
    start_nonterminal = None
    for nfa_a, nfa_z in GrammarParser(bnf_grammar).parse():
        #_dump_nfa(a, z)
        dfas = _make_dfas(nfa_a, nfa_z)
        #_dump_dfas(dfas)
        # oldlen = len(dfas)
        _simplify_dfas(dfas)
        # newlen = len(dfas)
        rule_to_dfas[nfa_a.from_rule] = dfas
        #print(nfa_a.from_rule, oldlen, newlen)

        if start_nonterminal is None:
            start_nonterminal = nfa_a.from_rule

    reserved_strings = {}
    for nonterminal, dfas in rule_to_dfas.items():
        for dfa_state in dfas:
            for terminal_or_nonterminal, next_dfa in dfa_state.arcs.items():
                if terminal_or_nonterminal in rule_to_dfas:
                    dfa_state.nonterminal_arcs[terminal_or_nonterminal] = next_dfa
                else:
                    transition = _make_transition(
                        token_namespace,
                        reserved_strings,
                        terminal_or_nonterminal
                    )
                    dfa_state.ilabel_to_plan[transition] = DFAPlan(next_dfa)

    return Grammar(start_nonterminal, rule_to_dfas, reserved_strings)


def _make_transition(token_namespace, reserved_syntax_strings, label):
    if label[0].isalpha():
        # A named token (e.g. NAME, NUMBER, STRING)
        return getattr(token_namespace, label)
    else:
        # Either a keyword or an operator
        assert label[0] in ('"', "'"), label
        assert not label.startswith('"""') and not label.startswith("'''")
        # TODO use literal_eval instead of a simple eval.
        value = literal_eval(label)
        try:
            return reserved_syntax_strings[value]
        except KeyError:
            r = reserved_syntax_strings[value] = ReservedString(value)
            return r
