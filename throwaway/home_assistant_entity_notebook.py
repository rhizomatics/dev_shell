import marimo

__generated_with = "0.8.18"
app = marimo.App(width="medium")


@app.cell
def __():
    import marimo as mo
    from homeassistant_api import Client 
    import polars as pl
    import altair as alt
    return Client, alt, mo, pl


@app.cell
def __():
    API_TOKEN="<api token here>"
    URL="http://homeassistant.local:8123/api"
    return API_TOKEN, URL


@app.cell
def __(API_TOKEN, Client, URL):
    client = Client(URL, API_TOKEN)
    return (client,)


@app.cell
def __(client):
    states = client.get_states()
    entity_groups = client.get_entities()
    domains = client.get_domains()
    components = client.get_components()
    entities=[(g,e.state.entity_id,e.state.attributes.get("friendly_name"),e.state.state,e.state.last_changed,e.state.last_updated) for g,gv in entity_groups.items() for e in gv.entities.values() ]
    return components, domains, entities, entity_groups, states


@app.cell
def __(components, domains, entities, entity_groups, mo, states):
    entity_stats = mo.stat(
        label="Entities",
        bordered=True,
        caption=f"In {len(entity_groups):,.0f} groups",
        value=f"{len(entities):,.0f}",
    )
    component_stats = mo.stat(
        label="Components",
        bordered=True,
        value=f"{len(components):,.0f}",
    )
    state_stats = mo.stat(
        label="States",
        bordered=True,
        value=f"{len(states):,.0f}",
    )
    action_stats = mo.stat(
        label="Actions",
        bordered=True,
        caption=f"In {len(domains):,.0f} domains",
        value=f"{sum(len(d.services) for d in domains.values()):,.0f}",
    )
    mo.hstack(
        [action_stats,entity_stats,component_stats,state_stats],
        widths="equal",
        gap=1,
    )
    return action_stats, component_stats, entity_stats, state_stats


@app.cell
def __(entity_groups, mo):
    how_many_group_buttons=8
    top_entity_types=sorted(entity_groups,key=lambda et:len(entity_groups[et].entities),reverse=True)[0:how_many_group_buttons]
    get_entity_type, set_entity_type = mo.state(top_entity_types[0])
    def entity_type_button(etype):

        def handle_click(v):
            set_entity_type(etype)
            return 1

        return mo.ui.button(
            label=etype,
            on_click=handle_click,
        )

    entity_type_buttons=[entity_type_button(etype) for etype in top_entity_types]

    mo.hstack(
        [
            mo.hstack(
                [
                    mo.md("Top entity types:") ] + entity_type_buttons
            ),
        ]
    )
    return (
        entity_type_button,
        entity_type_buttons,
        get_entity_type,
        how_many_group_buttons,
        set_entity_type,
        top_entity_types,
    )


@app.cell
def __(entities, get_entity_type, mo, pl):
    ent_pl = pl.DataFrame(entities,strict=False,orient="row",schema=("Entity Group","Entity ID","Name","State",("Last Changed",pl.datatypes.Datetime),("Last Updated",pl.datatypes.Datetime)))
    filtered_ent_pl=ent_pl.filter(pl.col("Entity Group") == get_entity_type()) if get_entity_type() is not None else ent_pl
    ent_table=mo.ui.table(filtered_ent_pl, selection="single")
    ent_table
    return ent_pl, ent_table, filtered_ent_pl


@app.cell
def __(alt, ent_table, entity_groups, mo, pl):
    if not ent_table.value.is_empty():
        selected_entity_group,selected_entity_slug=ent_table.value["Entity ID"][0].split(".")
        selected_entity=entity_groups[selected_entity_group].entities[selected_entity_slug]
        history=pl.DataFrame([(h.entity_id,h.state,h.attributes["unit_of_measurement"],h.last_changed,h.last_updated) for h in selected_entity.get_history().states],orient="row",strict=False,schema=("Entity ID","State","Unit",("Last Changed",pl.datatypes.Datetime),("Last Updated",pl.datatypes.Datetime)))
        widget=mo.ui.altair_chart(
        alt.Chart(history).mark_line().encode(
                x="Last Changed",
                y="State",
                tooltip=["Entity ID"]
            )
            .properties(width=500)
            .configure_scale(zero=False)
        )
    else:
        widget=mo.ui.text("No entity selected")
    widget
    return (
        history,
        selected_entity,
        selected_entity_group,
        selected_entity_slug,
        widget,
    )


@app.cell
def __(mo):
    mo.md(r"""**Data Frame View**""")
    return


@app.cell
def __(ent_pl, mo):
    mo.ui.dataframe(ent_pl)
    return


@app.cell
def __(mo):
    mo.md("""**Data Explorer**""")
    return


@app.cell
def __(ent_pl, mo):
    mo.ui.data_explorer(ent_pl)
    return


if __name__ == "__main__":
    app.run()
