import { Facts, NOT_RECORDED, NotRecorded, ProvenanceBlock, SyntheticBadge, utc } from './ReturnsCommon';
import type { EvidencePresence, ReturnDetail } from './types';

const PRESENCE_LABELS: [keyof EvidencePresence, string][] = [
  ['return_comments', 'Customer comments'],
  ['inspection_observations', 'Inspection observations'],
  ['shipment', 'Shipment'],
  ['delivery_events', 'Delivery events'],
  ['related_reviews', 'Related reviews'],
  ['related_complaints', 'Related complaints'],
  ['correction_cases', 'Correction cases'],
];

/**
 * One return (contracts §7, §9). Customer statements and inspection observations are shown in
 * separate columns side by side; conflicting evidence is never flagged, scored or resolved
 * (§3.1). Every text is the server's redacted form, rendered as React text only.
 */
export function ReturnDetailView({ detail }: { detail: ReturnDetail }) {
  const { ref, order, order_item: item, product, listing_snapshot: snapshot } = detail;
  return (
    <article className="returns-detail" aria-labelledby="return-detail-heading" data-testid="return-detail">
      <h3 id="return-detail-heading">
        Return {ref.return_id} {detail.provenance.is_synthetic && <SyntheticBadge />}
      </h3>
      <Facts
        items={[
          ['Dataset', `${ref.dataset_id} v${ref.dataset_version}`],
          ['Requested at', utc(detail.requested_at)],
          ['Status', detail.status],
          ['Quantity', detail.quantity],
          ['Received at', utc(detail.received_at)],
        ]}
      />
      <ProvenanceBlock provenance={detail.provenance} open />

      <section aria-labelledby="return-presence-heading" className="returns-card">
        <h4 id="return-presence-heading">Evidence recorded</h4>
        <ul className="returns-presence" data-testid="evidence-presence">
          {PRESENCE_LABELS.map(([key, label]) => (
            <li key={key} data-kind={key}>
              {label}: {detail.evidence_presence[key] === 0 ? NOT_RECORDED : detail.evidence_presence[key]}
            </li>
          ))}
        </ul>
      </section>

      <div className="returns-columns" data-testid="statements-and-observations">
        <section aria-labelledby="return-statements-heading" className="returns-card" data-testid="customer-statements">
          <h4 id="return-statements-heading">Customer statements</h4>
          <p className="fact-heading">What the customer said. Statements are not verified facts.</p>
          <Facts items={[['Reason code', detail.customer_statements.customer_reason_code]]} />
          {detail.customer_statements.comments.length === 0 ? (
            <NotRecorded />
          ) : (
            <ul className="returns-records">
              {detail.customer_statements.comments.map((comment) => (
                <li key={comment.comment_id} data-testid="customer-comment">
                  <p className="product-meta">
                    {comment.comment_id} · {utc(comment.submitted_at)}
                  </p>
                  <p className="returns-text">{comment.text}</p>
                  <ProvenanceBlock provenance={comment.provenance} />
                </li>
              ))}
            </ul>
          )}
        </section>
        <section
          aria-labelledby="return-observations-heading"
          className="returns-card"
          data-testid="inspection-observations"
        >
          <h4 id="return-observations-heading">Inspection observations</h4>
          <p className="fact-heading">What an inspector recorded. Not a decision on refund, fault or conduct.</p>
          {detail.inspection_observations.length === 0 ? (
            <NotRecorded />
          ) : (
            <ul className="returns-records">
              {detail.inspection_observations.map((observation) => (
                <li key={observation.observation_id} data-testid="inspection-observation">
                  <p className="product-meta">
                    {observation.observation_id} · {utc(observation.observed_at)} · {observation.inspector_ref}
                  </p>
                  <p>
                    Condition: <code>{observation.condition}</code>
                  </p>
                  {observation.note !== null && <p className="returns-text">{observation.note}</p>}
                  <ProvenanceBlock provenance={observation.provenance} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <section aria-labelledby="return-order-heading" className="returns-card">
        <h4 id="return-order-heading">Order and item</h4>
        <Facts
          items={[
            ['Order', order.order_id],
            ['Customer reference', order.customer_ref],
            ['Ordered at', utc(order.ordered_at)],
            ['Order item', item.order_item_id],
            ['Item quantity', item.quantity],
            ['Unit price', `${order.currency} ${item.unit_price}`],
          ]}
        />
        <ProvenanceBlock provenance={order.provenance} />
        <ProvenanceBlock provenance={item.provenance} />
      </section>

      <section aria-labelledby="return-product-heading" className="returns-card">
        <h4 id="return-product-heading">Product and listing snapshot</h4>
        <p className="fact-heading">Current catalog record</p>
        <Facts
          items={[
            ['Product', product.product_id],
            ['SKU', product.sku],
            ['Title', product.title],
            ['Brand', product.brand],
            ['Category', product.category],
          ]}
        />
        <p className="fact-heading">Listing snapshot at order time</p>
        <Facts
          items={[
            ['Catalog', `${snapshot.catalog_dataset_id} version ${snapshot.catalog_dataset_version}`],
            ['Raw line SHA-256', snapshot.raw_sha256],
            ['Matches current catalog', snapshot.matches_current_catalog ? 'yes' : 'no'],
            ['Title', snapshot.title],
            ['Brand', snapshot.brand],
            ['Category', snapshot.category],
            ['Price (as listed)', `${snapshot.currency} ${String(snapshot.price)}`],
          ]}
        />
      </section>

      <section aria-labelledby="return-shipment-heading" className="returns-card" data-testid="shipment">
        <h4 id="return-shipment-heading">Shipment and delivery</h4>
        {detail.shipment === null ? (
          <NotRecorded />
        ) : (
          <>
            <Facts
              items={[
                ['Shipment', detail.shipment.shipment_id],
                ['Carrier', detail.shipment.carrier_ref],
                ['Status', detail.shipment.status],
                ['Shipped at', utc(detail.shipment.shipped_at)],
                ['Promised by', utc(detail.shipment.promised_delivery_by)],
                ['Delivered at', utc(detail.shipment.delivered_at)],
              ]}
            />
            <ProvenanceBlock provenance={detail.shipment.provenance} />
            {detail.shipment.delivery_events.length === 0 ? (
              <NotRecorded />
            ) : (
              <ol className="returns-records" aria-label="Delivery events">
                {detail.shipment.delivery_events.map((event) => (
                  <li key={event.event_id}>
                    <p>
                      {utc(event.occurred_at)} · <code>{event.event_type}</code> · {event.source}
                    </p>
                    <ProvenanceBlock provenance={event.provenance} />
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </section>

      <section aria-labelledby="return-reviews-heading" className="returns-card" data-testid="related-reviews">
        <h4 id="return-reviews-heading">Related reviews</h4>
        {detail.related_reviews.length === 0 ? (
          <NotRecorded />
        ) : (
          <ul className="returns-records">
            {detail.related_reviews.map((review) => (
              <li key={review.review_id}>
                <p className="product-meta">
                  {review.review_id} · {utc(review.submitted_at)} · rating {review.rating}
                </p>
                {review.text !== null && <p className="returns-text">{review.text}</p>}
                <ProvenanceBlock provenance={review.provenance} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="return-complaints-heading" className="returns-card" data-testid="related-complaints">
        <h4 id="return-complaints-heading">Related complaints</h4>
        {detail.related_complaints.length === 0 ? (
          <NotRecorded />
        ) : (
          <ul className="returns-records">
            {detail.related_complaints.map((complaint) => (
              <li key={complaint.complaint_id}>
                <p className="product-meta">
                  {complaint.complaint_id} · {utc(complaint.opened_at)} · {complaint.channel}
                  {complaint.customer_topic !== null && ` · ${complaint.customer_topic}`}
                </p>
                <p className="returns-text">{complaint.text}</p>
                <ProvenanceBlock provenance={complaint.provenance} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="return-cases-heading" className="returns-card" data-testid="correction-cases">
        <h4 id="return-cases-heading">Correction cases</h4>
        {detail.correction_cases.length === 0 ? (
          <NotRecorded />
        ) : (
          <ul className="returns-records">
            {detail.correction_cases.map((correction) => (
              <li key={correction.case_id}>
                <p>
                  {correction.case_id} · focus area <code>{correction.focus_area}</code> · {correction.status} ·
                  opened {utc(correction.opened_at)} · {correction.evidence_count} evidence link
                  {correction.evidence_count === 1 ? '' : 's'}
                </p>
                <ProvenanceBlock provenance={correction.provenance} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </article>
  );
}
